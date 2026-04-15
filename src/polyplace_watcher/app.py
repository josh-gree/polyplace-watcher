import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from polyplace_contracts.deploy import Deployment
from polyplace_watcher.grid_store import GridStore
from polyplace_watcher.observability import configure_logging
from polyplace_watcher.watcher import Watcher

logger = logging.getLogger(__name__)


def _watcher_from_env(store: GridStore) -> Watcher:
    http_url = os.environ["WEB3_HTTP_URL"]
    ws_url = os.environ["WEB3_WS_URL"]
    start_block = int(os.environ["START_BLOCK"])
    deployment = Deployment(
        grid=os.environ["GRID_ADDRESS"],
        token=os.environ["TOKEN_ADDRESS"],
        faucet=os.environ["FAUCET_ADDRESS"],
    )
    return Watcher(http_url=http_url, ws_url=ws_url, deployment=deployment, start_block=start_block, store=store)


async def _snapshot_loop(store: GridStore, path: Path, interval: int) -> None:
    last_saved_etag: str | None = None
    logger.info(
        "snapshot_loop_started",
        extra={"component": "snapshot", "snapshot_path": path, "snapshot_interval_seconds": interval},
    )
    while True:
        await asyncio.sleep(interval)
        etag = store.etag
        if store.last_block is not None and etag != last_saved_etag:
            logger.info(
                "snapshot_saving",
                extra={
                    "component": "snapshot",
                    "snapshot_path": path,
                    "etag": etag,
                    "last_block": store.last_block,
                    "last_log_index": store.last_log_index,
                },
            )
            await store.save_snapshot(path)
            last_saved_etag = etag


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    store = GridStore()
    snapshot_path = Path(os.environ.get("SNAPSHOT_PATH", "snapshot.json"))
    snapshot_interval = int(os.environ.get("SNAPSHOT_INTERVAL", "60"))

    logger.info(
        "service_config_loaded",
        extra={
            "component": "app",
            "config": {
                "SNAPSHOT_PATH": snapshot_path,
                "SNAPSHOT_INTERVAL": snapshot_interval,
            },
        },
    )

    if snapshot_path.exists():
        logger.info(
            "snapshot_loading",
            extra={"component": "snapshot", "snapshot_path": snapshot_path},
        )
        store.load_snapshot(snapshot_path)

    watcher = _watcher_from_env(store)
    watch_task = asyncio.create_task(watcher.watch())
    snapshot_task = asyncio.create_task(_snapshot_loop(store, snapshot_path, snapshot_interval))
    app.state.store = store
    app.state.watcher = watcher
    try:
        yield
    finally:
        logger.info("service_stopping", extra={"component": "app"})
        watch_task.cancel()
        snapshot_task.cancel()
        await asyncio.gather(watch_task, snapshot_task, return_exceptions=True)
        if store.last_block is not None:
            await store.save_snapshot(snapshot_path)
        logger.info("service_stopped", extra={"component": "app"})


app = FastAPI(lifespan=lifespan)


@app.get("/grid")
async def get_grid(request: Request) -> Response:
    store: GridStore = request.app.state.store
    etag, content = await store.compressed_snapshot()

    if request.headers.get("if-none-match") == etag:
        logger.debug(
            "grid_response_not_modified",
            extra={
                "component": "api",
                "path": "/grid",
                "status_code": 304,
                "etag": etag,
            },
        )
        return Response(status_code=304)

    logger.debug(
        "grid_response_returned",
        extra={
            "component": "api",
            "path": "/grid",
            "status_code": 200,
            "etag": etag,
            "byte_count": len(content),
        },
    )
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"ETag": etag, "Cache-Control": "no-cache", "Content-Encoding": "gzip"},
    )


@app.websocket("/ws")
async def websocket_grid(websocket: WebSocket) -> None:
    store: GridStore = websocket.app.state.store
    await websocket.accept()
    queue = store.subscribe()
    try:
        _, snapshot = await store.compressed_snapshot()
        await websocket.send_bytes(snapshot)
        while True:
            cell_id, r, g, b, renter, expires_at = await queue.get()
            await websocket.send_text(json.dumps({
                "i": cell_id, "r": r, "g": g, "b": b,
                "renter": renter, "expires_at": expires_at,
            }))
    except WebSocketDisconnect:
        pass
    finally:
        store.unsubscribe(queue)


if _frontend_dir := os.environ.get("FRONTEND_DIR"):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
