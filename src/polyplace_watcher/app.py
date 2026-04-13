import asyncio
import gzip
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response

from polyplace_contracts.deploy import Deployment
from polyplace_watcher.watcher import Watcher


@dataclass
class _GridCache:
    last_block: int = -1
    data: bytes = field(default_factory=bytes)


def _watcher_from_env() -> Watcher:
    http_url = os.environ["WEB3_HTTP_URL"]
    ws_url = os.environ["WEB3_WS_URL"]
    deployment = Deployment(
        grid=os.environ["GRID_ADDRESS"],
        token=os.environ["TOKEN_ADDRESS"],
        faucet=os.environ["FAUCET_ADDRESS"],
    )
    return Watcher(http_url=http_url, ws_url=ws_url, deployment=deployment)


async def _snapshot_loop(watcher: Watcher, path: Path, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        if watcher._last_block is not None:
            watcher.save_snapshot(path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    watcher = _watcher_from_env()
    snapshot_path = Path(os.environ.get("SNAPSHOT_PATH", "snapshot.json"))
    snapshot_interval = int(os.environ.get("SNAPSHOT_INTERVAL", "60"))

    if snapshot_path.exists():
        watcher.load_snapshot(snapshot_path)

    watch_task = asyncio.create_task(watcher.watch())
    snapshot_task = asyncio.create_task(
        _snapshot_loop(watcher, snapshot_path, snapshot_interval)
    )
    app.state.watcher = watcher
    app.state.grid_cache = _GridCache()
    try:
        yield
    finally:
        watch_task.cancel()
        snapshot_task.cancel()
        await asyncio.gather(watch_task, snapshot_task, return_exceptions=True)
        if watcher._last_block is not None:
            watcher.save_snapshot(snapshot_path)


app = FastAPI(lifespan=lifespan)


@app.get("/grid")
async def get_grid(request: Request) -> Response:
    watcher: Watcher = request.app.state.watcher
    cache: _GridCache = request.app.state.grid_cache

    last_block = watcher._last_block
    etag = f'"{last_block}"'

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)

    if cache.last_block != last_block:
        cache.data = gzip.compress(watcher.grid.to_bytes(), compresslevel=1)
        cache.last_block = last_block

    return Response(
        content=cache.data,
        media_type="application/octet-stream",
        headers={
            "ETag": etag,
            "Cache-Control": "no-cache",
            "Content-Encoding": "gzip",
        },
    )
