import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response

from polyplace_contracts.deploy import Deployment
from polyplace_watcher.grid_store import GridStore
from polyplace_watcher.watcher import Watcher


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
    while True:
        await asyncio.sleep(interval)
        etag = store.etag
        if store.last_block is not None and etag != last_saved_etag:
            await store.save_snapshot(path)
            last_saved_etag = etag


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    store = GridStore()
    snapshot_path = Path(os.environ.get("SNAPSHOT_PATH", "snapshot.json"))
    snapshot_interval = int(os.environ.get("SNAPSHOT_INTERVAL", "60"))

    if snapshot_path.exists():
        store.load_snapshot(snapshot_path)

    watcher = _watcher_from_env(store)
    watch_task = asyncio.create_task(watcher.watch())
    snapshot_task = asyncio.create_task(_snapshot_loop(store, snapshot_path, snapshot_interval))
    app.state.store = store
    app.state.watcher = watcher
    try:
        yield
    finally:
        watch_task.cancel()
        snapshot_task.cancel()
        await asyncio.gather(watch_task, snapshot_task, return_exceptions=True)
        if store.last_block is not None:
            await store.save_snapshot(snapshot_path)


app = FastAPI(lifespan=lifespan)


@app.get("/grid")
async def get_grid(request: Request) -> Response:
    store: GridStore = request.app.state.store
    etag, content = await store.compressed_snapshot()

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"ETag": etag, "Cache-Control": "no-cache", "Content-Encoding": "gzip"},
    )
