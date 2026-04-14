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


# --- compressed_bytes ---


async def test_compressed_bytes_returns_valid_gzip() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    data = await store.compressed_bytes()
    rt = Grid.from_bytes(gzip.decompress(data))
    assert rt.get(0) is not None


async def test_compressed_bytes_on_empty_store() -> None:
    store = GridStore()
    data = await store.compressed_bytes()
    rt = Grid.from_bytes(gzip.decompress(data))
    assert rt._cells == {}


async def test_compressed_bytes_cached_on_second_call() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    first = await store.compressed_bytes()
    second = await store.compressed_bytes()
    assert first is second


async def test_compressed_bytes_invalidated_after_apply() -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    first = await store.compressed_bytes()
    store.apply(CellColorUpdated(cell_id=0, renter=ADDR_A, color=0xFF8800), block=1, log_index=1)
    second = await store.compressed_bytes()
    assert first is not second


async def test_compressed_bytes_uses_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    to_thread_calls: list[object] = []

    async def fake_to_thread(func: object, *args: object, **kwargs: object) -> object:
        to_thread_calls.append(func)
        if callable(func):
            return func(*args, **kwargs)  # type: ignore[operator]

    monkeypatch.setattr(grid_store_module.asyncio, "to_thread", fake_to_thread)

    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=1, log_index=0)
    await store.compressed_bytes()

    assert gzip.compress in to_thread_calls


# --- snapshot ---


def test_save_snapshot_raises_without_last_block(tmp_path: Path) -> None:
    store = GridStore()
    with pytest.raises(ValueError):
        store.save_snapshot(tmp_path / "snap.json")


def test_save_snapshot_writes_file(tmp_path: Path) -> None:
    store = GridStore()
    store.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=10, log_index=0)
    path = tmp_path / "snap.json"
    store.save_snapshot(path)
    assert path.exists()


def test_load_snapshot_restores_last_block(tmp_path: Path) -> None:
    path = tmp_path / "snap.json"
    path.write_text(Snapshot(last_block=99, cells={}).model_dump_json())
    store = GridStore()
    store.load_snapshot(path)
    assert store.last_block == 99


def test_load_snapshot_restores_cells(tmp_path: Path) -> None:
    writer = GridStore()
    writer.apply(CellRented(cell_id=7, renter=ADDR_A, expires_at=EXPIRES_AT), block=5, log_index=0)
    path = tmp_path / "snap.json"
    writer.save_snapshot(path)

    reader = GridStore()
    reader.load_snapshot(path)
    assert reader.get(7) == writer.get(7)


def test_snapshot_round_trip_preserves_last_block(tmp_path: Path) -> None:
    writer = GridStore()
    writer.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT), block=42, log_index=3)
    path = tmp_path / "snap.json"
    writer.save_snapshot(path)

    reader = GridStore()
    reader.load_snapshot(path)
    assert reader.last_block == 42
