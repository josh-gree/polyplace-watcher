"""The ASGI app (SPEC §3, §5, D1): /grid, /ws, /health, and the lifespan
that runs the watch task. ``polyplace_watcher.app:app`` is the frozen
deploy entrypoint; env is read at startup, never at import.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pythonjsonlogger.json import JsonFormatter

from .config import Config, scrub_url
from .core import Grid
from .snapshot import load_snapshot
from .watch import Store, checkpoint, save_if_changed, watch
from .wire import encode_grid

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Structured JSON to stdout — Fly captures stdout (SPEC §8)."""
    package_logger = logging.getLogger("polyplace_watcher")
    if package_logger.handlers:  # already configured (module reload, TestClient re-entry)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    config = Config.from_env()
    log.info(
        "watcher_starting",
        extra={
            "grid_address": config.grid_address,
            "start_block": config.start_block,
            "http_url": scrub_url(config.web3_http_url),
            "ws_url": scrub_url(config.web3_ws_url),
            "snapshot_path": str(config.snapshot_path),
            "backfill_chunk_size": config.backfill_chunk_size,
            "git_sha": config.git_sha,
        },
    )

    store = Store()
    loaded = load_snapshot(config.snapshot_path)
    if loaded is not None:
        store.grid, store.last_block, store.last_log_index = loaded
        log.info(
            "snapshot_loaded",
            extra={"path": str(config.snapshot_path), "last_block": store.last_block},
        )

    app.state.config = config
    app.state.store = store
    app.state.grid_cache = (None, b"")  # (etag, gzipped body) of the last /grid encode
    app.state.grid_cache_lock = asyncio.Lock()
    # The API serves from this moment, mid-backfill included — no readiness
    # gate, by design (SPEC §4, decision D4).
    app.state.watch_task = asyncio.create_task(watch(config, store))
    app.state.checkpoint_task = asyncio.create_task(checkpoint(config, store))
    yield
    app.state.watch_task.cancel()
    app.state.checkpoint_task.cancel()
    await asyncio.gather(app.state.watch_task, app.state.checkpoint_task, return_exceptions=True)
    save_if_changed(config, store)  # graceful shutdown writes the snapshot (SPEC §4)
    log.info("watcher_stopped")


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def cors(request: Request, call_next):
    """Hand-rolled because CORS_ORIGINS is read at startup while Starlette's
    CORSMiddleware wants its origins at app-construction (import) time.
    GETs from the frontend are "simple" CORS requests — no preflight needed.
    """
    response = await call_next(request)
    origin = request.headers.get("origin")
    if origin in request.app.state.config.cors_origins:
        response.headers["access-control-allow-origin"] = origin
        response.headers["access-control-expose-headers"] = "ETag"
        response.headers["vary"] = "Origin"
    return response


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    store: Store = request.app.state.store
    alive = not request.app.state.watch_task.done()
    return JSONResponse(
        {
            "last_block": store.last_block,
            "last_log_index": store.last_log_index,
            "git_sha": request.app.state.config.git_sha,
            "watcher_alive": alive,
        },
        # D1: a retrying task is alive and healthy; a dead one turns Fly's
        # status-code check red, and Fly restarts the machine.
        status_code=200 if alive else 503,
    )


def _etag(store: Store) -> str:
    return f'"{store.last_block or 0}-{store.last_log_index or 0}"'


@app.get("/grid")
async def grid(request: Request) -> Response:
    store: Store = request.app.state.store
    etag = _etag(store)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"etag": etag, "cache-control": "no-cache"})
    # Encode at most once per grid state change, not once per request: the
    # grid changes at chain speed but is read at user speed, and a full-grid
    # encode+gzip would otherwise stall the loop ~1s per cold request.
    cached_etag, body = request.app.state.grid_cache
    if cached_etag != etag:
        etag, body = await _refresh_grid_cache(request.app)
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={"content-encoding": "gzip", "etag": etag, "cache-control": "no-cache"},
    )


async def _refresh_grid_cache(app: FastAPI) -> tuple[str, bytes]:
    """Re-encode the grid off-loop, single-flight.

    The lock keeps N concurrent cold requests to one encode: by the time a
    waiter acquires it, the winner has usually already stored the bytes, so
    the double-check serves everyone from cache. The cells dict is copied on
    the loop (so the watch task can't resize it mid-iteration in the worker
    thread); the Cell objects themselves are shared, which is why encode_grid
    snapshots each cell's fields up front. The bytes may end up slightly
    newer than a concurrent request's first watermark read — SPEC §5 allows
    /grid to serve bytes that don't exactly match the moving grid, and the
    returned etag always matches the returned body.
    """
    async with app.state.grid_cache_lock:
        store: Store = app.state.store
        etag = _etag(store)
        cached_etag, body = app.state.grid_cache
        if cached_etag == etag:
            return etag, body
        snapshot = Grid()
        snapshot.cells = dict(store.grid.cells)  # no await since the etag read: pair is consistent
        # Level 1: the PLG payload barely compresses better at higher levels
        # (8.75 vs 8.77 MB on a full grid) and level 9 costs 5x the CPU.
        body = await asyncio.to_thread(lambda: gzip.compress(encode_grid(snapshot), 1))
        app.state.grid_cache = (etag, body)
        return etag, body


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Stream per-cell deltas from now on; history comes from /grid (SPEC §5)."""
    await websocket.accept()
    store: Store = websocket.app.state.store
    deltas = store.subscribe()
    forward = asyncio.create_task(_forward_deltas(websocket, deltas))
    try:
        # Clients never send; receiving is how an idle subscriber's
        # disconnect is noticed (otherwise it would pin graceful shutdown).
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        forward.cancel()
        await asyncio.gather(forward, return_exceptions=True)
        store.unsubscribe(deltas)


async def _forward_deltas(websocket: WebSocket, deltas: asyncio.Queue[dict]) -> None:
    while True:
        await websocket.send_text(json.dumps(await deltas.get()))
