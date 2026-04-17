import asyncio
import gzip
import logging
from pathlib import Path
from threading import Lock

from polyplace_watcher.events import CellColorUpdated, CellRented
from polyplace_watcher.grid import Cell, Grid
from polyplace_watcher.snapshot import Snapshot

logger = logging.getLogger(__name__)

_UNSET = object()
_StateKey = tuple[int | None, int | None]


def _compress_grid(grid: Grid) -> bytes:
    return gzip.compress(grid.to_bytes(), compresslevel=1)


class GridStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._grid = Grid()
        self._last_block: int | None = None
        self._last_log_index: int | None = None
        self._cache_key: object = _UNSET
        self._cache_bytes: bytes = b""
        self._subscribers: set[asyncio.Queue[tuple[int, int, int, int, str | None, int | None]]] = set()

    def subscribe(self) -> asyncio.Queue[tuple[int, int, int, int, str | None, int | None]]:
        q: asyncio.Queue[tuple[int, int, int, int, str | None, int | None]] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[tuple[int, int, int, int, str | None, int | None]]) -> None:
        self._subscribers.discard(q)

    def _state_key(self) -> _StateKey:
        return (self._last_block, self._last_log_index)

    @staticmethod
    def _etag_for_key(key: _StateKey) -> str:
        return f'"{key[0]}.{key[1]}"'

    @property
    def last_block(self) -> int | None:
        return self._last_block

    @property
    def last_log_index(self) -> int | None:
        return self._last_log_index

    @property
    def etag(self) -> str:
        return self._etag_for_key(self._state_key())

    def apply(self, event: CellRented | CellColorUpdated, block: int, log_index: int) -> None:
        with self._lock:
            self._grid.apply(event)
            self._last_block = block
            self._last_log_index = log_index
            self._cache_key = _UNSET
            cell = self._grid.get(event.cell_id)
            color = cell.color if cell else None

        if color is not None:
            expires_ts = int(cell.expires_at.timestamp()) if cell.expires_at else None
            update = (event.cell_id, color.r, color.g, color.b, cell.renter, expires_ts)
            for q in list(self._subscribers):
                q.put_nowait(update)

        logger.debug(
            "grid_event_applied",
            extra={
                "component": "grid_store",
                "event_type": type(event).__name__,
                "cell_id": event.cell_id,
                "block": block,
                "log_index": log_index,
            },
        )

    def get(self, cell_id: int) -> Cell | None:
        return self._grid.get(cell_id)

    async def compressed_snapshot(self) -> tuple[str, bytes]:
        with self._lock:
            key = self._state_key()
            etag = self._etag_for_key(key)
            if key == self._cache_key:
                logger.debug(
                    "compressed_snapshot_cache_hit",
                    extra={
                        "component": "grid_store",
                        "etag": etag,
                        "byte_count": len(self._cache_bytes),
                    },
                )
                return etag, self._cache_bytes
            grid = self._grid.clone()

        logger.debug(
            "compressed_snapshot_cache_miss",
            extra={"component": "grid_store", "etag": etag},
        )
        data = await asyncio.to_thread(_compress_grid, grid)

        with self._lock:
            current_key = self._state_key()
            if current_key == self._cache_key:
                # A concurrent request already stored a newer snapshot; use it.
                return self._etag_for_key(current_key), self._cache_bytes
            # Store what we computed — may be slightly stale if the grid moved
            # during compression, but the WebSocket stream covers any delta.
            self._cache_key = key
            self._cache_bytes = data
            logger.debug(
                "compressed_snapshot_cache_stored",
                extra={
                    "component": "grid_store",
                    "etag": etag,
                    "byte_count": len(data),
                },
            )
            return etag, data

    async def save_snapshot(self, path: Path) -> None:
        with self._lock:
            if self._last_block is None:
                raise ValueError("No blocks processed yet; cannot save snapshot.")
            snap = Snapshot(
                last_block=self._last_block,
                last_log_index=self._last_log_index,
                cells=self._grid.cells_snapshot(),
            )
        await asyncio.to_thread(lambda: path.write_text(snap.model_dump_json()))
        logger.info(
            "snapshot_saved",
            extra={
                "component": "grid_store",
                "snapshot_path": path,
                "last_block": snap.last_block,
                "last_log_index": snap.last_log_index,
                "cell_count": len(snap.cells),
            },
        )

    def load_snapshot(self, path: Path) -> None:
        snap = Snapshot.model_validate_json(path.read_text())
        with self._lock:
            self._grid.replace_cells(snap.cells)
            self._last_block = snap.last_block
            self._last_log_index = snap.last_log_index
            self._cache_key = _UNSET
        logger.info(
            "snapshot_loaded",
            extra={
                "component": "grid_store",
                "snapshot_path": path,
                "last_block": snap.last_block,
                "last_log_index": snap.last_log_index,
                "cell_count": len(snap.cells),
            },
        )
