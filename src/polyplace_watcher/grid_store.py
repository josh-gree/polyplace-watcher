import asyncio
import gzip
from pathlib import Path

from polyplace_watcher.events import CellColorUpdated, CellRented
from polyplace_watcher.grid import Cell, Grid
from polyplace_watcher.snapshot import Snapshot

_UNSET = object()


class GridStore:
    def __init__(self) -> None:
        self._grid = Grid()
        self._last_block: int | None = None
        self._last_log_index: int | None = None
        self._cache_key: object = _UNSET
        self._cache_bytes: bytes = b""

    @property
    def last_block(self) -> int | None:
        return self._last_block

    @property
    def last_log_index(self) -> int | None:
        return self._last_log_index

    @property
    def etag(self) -> str:
        return f'"{self._last_block}.{self._last_log_index}"'

    def apply(self, event: CellRented | CellColorUpdated, block: int, log_index: int) -> None:
        self._grid.apply(event)
        self._last_block = block
        self._last_log_index = log_index

    def get(self, cell_id: int) -> Cell | None:
        return self._grid.get(cell_id)

    async def compressed_bytes(self) -> bytes:
        key = (self._last_block, self._last_log_index)
        if key != self._cache_key:
            self._cache_bytes = await asyncio.to_thread(gzip.compress, self._grid.to_bytes(), compresslevel=1)
            self._cache_key = key
        return self._cache_bytes

    async def save_snapshot(self, path: Path) -> None:
        if self._last_block is None:
            raise ValueError("No blocks processed yet; cannot save snapshot.")
        snap = Snapshot(last_block=self._last_block, cells=dict(self._grid._cells))
        await asyncio.to_thread(lambda: path.write_text(snap.model_dump_json()))

    def load_snapshot(self, path: Path) -> None:
        snap = Snapshot.model_validate_json(path.read_text())
        self._grid._cells = snap.cells
        self._last_block = snap.last_block
        self._cache_key = _UNSET
