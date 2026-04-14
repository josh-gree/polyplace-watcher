import asyncio
import gzip
from pathlib import Path

import pytest

import polyplace_watcher.grid_store as grid_store_module
from polyplace_watcher.events import CellColorUpdated, CellRented
from polyplace_watcher.grid import Grid
from polyplace_watcher.grid_store import GridStore
from polyplace_watcher.snapshot import Snapshot

ADDR_A = "0x" + "ab" * 20
EXPIRES_AT = 1712345678


# --- initial state ---


def test_initial_last_block_is_none() -> None:
    store = GridStore()
    assert store.last_block is None


def test_initial_last_log_index_is_none() -> None:
    store = GridStore()
    assert store.last_log_index is None


def test_initial_get_returns_none() -> None:
    store = GridStore()
    assert store.get(0) is None


def test_initial_etag() -> None:
    store = GridStore()
    assert store.etag == '"None.None"'


# --- apply ---


def test_apply_updates_last_block() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=10, log_index=2)
    assert store.last_block == 10


def test_apply_updates_last_log_index() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=10, log_index=2)
    assert store.last_log_index == 2


def test_apply_makes_cell_visible_via_get() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=10, log_index=0)
    assert store.get(0) is not None


def test_apply_updates_etag() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=42, log_index=5)
    assert store.etag == '"42.5"'


def test_apply_color_update() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    store.apply(CellColorUpdated(cell_id=0, renter=ADDR_A, color=0xFF8800), block=1, log_index=1)
    cell = store.get(0)
    assert cell is not None
    assert cell.color is not None
    assert cell.color.r == 255
    assert cell.color.g == 136
    assert cell.color.b == 0


# --- compressed_snapshot ---


async def test_compressed_snapshot_returns_valid_gzip() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    _, data = await store.compressed_snapshot()
    rt = Grid.from_bytes(gzip.decompress(data))
    assert rt.get(0) is not None


async def test_compressed_snapshot_on_empty_store() -> None:
    store = GridStore()
    _, data = await store.compressed_snapshot()
    rt = Grid.from_bytes(gzip.decompress(data))
    assert rt._cells == {}


async def test_compressed_snapshot_cached_on_second_call() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    _, first = await store.compressed_snapshot()
    _, second = await store.compressed_snapshot()
    assert first is second


async def test_compressed_snapshot_invalidated_after_apply() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    _, first = await store.compressed_snapshot()
    store.apply(CellColorUpdated(cell_id=0, renter=ADDR_A, color=0xFF8800), block=1, log_index=1)
    _, second = await store.compressed_snapshot()
    assert first is not second


async def test_compressed_snapshot_serializes_in_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    inside_to_thread = False
    to_bytes_inside_to_thread: list[bool] = []
    original_to_bytes = Grid.to_bytes

    async def fake_to_thread(func: object, *args: object, **kwargs: object) -> object:
        nonlocal inside_to_thread
        inside_to_thread = True
        try:
            if callable(func):
                return func(*args, **kwargs)  # type: ignore[operator]
        finally:
            inside_to_thread = False

    def tracked_to_bytes(self: Grid) -> bytes:
        to_bytes_inside_to_thread.append(inside_to_thread)
        return original_to_bytes(self)

    monkeypatch.setattr(grid_store_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(Grid, "to_bytes", tracked_to_bytes)

    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    await store.compressed_snapshot()

    assert to_bytes_inside_to_thread == [True]


async def test_compressed_snapshot_discards_stale_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    mutated_during_serialization = False

    async def fake_to_thread(func: object, *args: object, **kwargs: object) -> object:
        nonlocal mutated_during_serialization
        if not mutated_during_serialization:
            mutated_during_serialization = True
            store.apply(CellRented(cell_id=1, renter=ADDR_A, expires_at=EXPIRES_AT), block=2, log_index=0)
        if callable(func):
            return func(*args, **kwargs)  # type: ignore[operator]

    monkeypatch.setattr(grid_store_module.asyncio, "to_thread", fake_to_thread)

    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)

    etag, data = await store.compressed_snapshot()
    rt = Grid.from_bytes(gzip.decompress(data))

    assert etag == '"2.0"'
    assert rt.get(0) is not None
    assert rt.get(1) is not None


# --- snapshot ---


async def test_save_snapshot_raises_without_last_block(tmp_path: Path) -> None:
    store = GridStore()
    with pytest.raises(ValueError):
        await store.save_snapshot(tmp_path / "snap.json")


async def test_save_snapshot_writes_file(tmp_path: Path) -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=10, log_index=0)
    path = tmp_path / "snap.json"
    await store.save_snapshot(path)
    assert path.exists()


def test_load_snapshot_restores_last_block(tmp_path: Path) -> None:
    path = tmp_path / "snap.json"
    path.write_text(Snapshot(last_block=99, cells={}).model_dump_json())
    store = GridStore()
    store.load_snapshot(path)
    assert store.last_block == 99


async def test_load_snapshot_restores_cells(tmp_path: Path) -> None:
    writer = GridStore()
    writer.apply(CellRented(cell_id=7, renter=ADDR_A, expires_at=EXPIRES_AT), block=5, log_index=0)
    path = tmp_path / "snap.json"
    await writer.save_snapshot(path)

    reader = GridStore()
    reader.load_snapshot(path)
    assert reader.get(7) == writer.get(7)


async def test_load_snapshot_invalidates_compression_cache(tmp_path: Path) -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    await store.compressed_snapshot()

    writer = GridStore()
    writer.apply(CellRented(cell_id=5, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    path = tmp_path / "snap.json"
    await writer.save_snapshot(path)

    store.load_snapshot(path)
    _, after = await store.compressed_snapshot()
    rt = Grid.from_bytes(gzip.decompress(after))

    assert rt.get(0) is None
    assert rt.get(5) == writer.get(5)


async def test_snapshot_round_trip_preserves_last_block(tmp_path: Path) -> None:
    writer = GridStore()
    writer.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=42, log_index=3)
    path = tmp_path / "snap.json"
    await writer.save_snapshot(path)

    reader = GridStore()
    reader.load_snapshot(path)
    assert reader.last_block == 42


async def test_snapshot_round_trip_preserves_last_log_index(tmp_path: Path) -> None:
    writer = GridStore()
    writer.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=42, log_index=3)
    path = tmp_path / "snap.json"
    await writer.save_snapshot(path)

    reader = GridStore()
    reader.load_snapshot(path)
    assert reader.last_log_index == 3


async def test_save_snapshot_captures_state_on_event_loop(tmp_path: Path) -> None:
    """Snapshot must reflect state at call time, not at I/O time."""
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    path = tmp_path / "snap.json"

    write_calls: list[object] = []

    async def fake_to_thread(func: object, *args: object, **kwargs: object) -> object:
        # mutate the store before the write happens — should not affect snapshot
        store.apply(CellRented(cell_id=1, renter=ADDR_A, expires_at=EXPIRES_AT), block=2, log_index=0)
        write_calls.append(func)
        if callable(func):
            return func(*args, **kwargs)  # type: ignore[operator]

    import polyplace_watcher.grid_store as grid_store_module
    original = grid_store_module.asyncio.to_thread
    grid_store_module.asyncio.to_thread = fake_to_thread  # type: ignore[assignment]
    try:
        await store.save_snapshot(path)
    finally:
        grid_store_module.asyncio.to_thread = original

    snap = Snapshot.model_validate_json(path.read_text())
    assert snap.last_block == 1
    assert 1 not in snap.cells
