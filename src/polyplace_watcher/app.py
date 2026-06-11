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


@app.get("/grid")
async def grid(request: Request) -> Response:
    store: Store = request.app.state.store
    etag = f'"{store.last_block or 0}-{store.last_log_index or 0}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"etag": etag, "cache-control": "no-cache"})
    # Encode at most once per grid state change, not once per request: the
    # grid changes at chain speed but is read at user speed, and a full-grid
    # encode+gzip stalls the event loop for ~1s. No await separates the
    # watermark read from the cache store, so check-then-act is atomic here.
    cached_etag, body = request.app.state.grid_cache
    if cached_etag != etag:
        body = gzip.compress(encode_grid(store.grid))
        request.app.state.grid_cache = (etag, body)
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={"content-encoding": "gzip", "etag": etag, "cache-control": "no-cache"},
    )


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
