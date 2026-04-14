import asyncio
from contextlib import suppress
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import polyplace_watcher.app as app_module
from polyplace_watcher.app import _GridCache, _snapshot_loop, app, lifespan
from polyplace_watcher.events import CellColorUpdated, CellRented
from polyplace_watcher.grid import Grid
from polyplace_watcher.snapshot import Snapshot

ADDR_A = "0x" + "ab" * 20
EXPIRES_AT = 1712345678


def _make_client(grid: Grid, last_block: int | None, last_log_index: int | None = None) -> AsyncClient:
    """Return a test client with app.state pre-populated, bypassing lifespan."""
    app.state.watcher = _FakeWatcher(grid, last_block, last_log_index)
    app.state.grid_cache = _GridCache()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class _FakeWatcher:
    def __init__(self, grid: Grid, last_block: int | None, last_log_index: int | None = None) -> None:
        self.grid = grid
        self._last_block = last_block
        self._last_log_index = last_log_index


@pytest.fixture
def empty_grid_client() -> AsyncClient:
    return _make_client(Grid(), last_block=None)


@pytest.fixture
def populated_grid_client() -> AsyncClient:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT))
    grid.apply(CellColorUpdated(cell_id=0, renter=ADDR_A, color=0xFF8800))
    return _make_client(grid, last_block=42, last_log_index=5)


async def test_get_grid_returns_200(populated_grid_client: AsyncClient) -> None:
    async with populated_grid_client as client:
        response = await client.get("/grid")
    assert response.status_code == 200


async def test_get_grid_content_type(populated_grid_client: AsyncClient) -> None:
    async with populated_grid_client as client:
        response = await client.get("/grid")
    assert response.headers["content-type"] == "application/octet-stream"


async def test_get_grid_content_encoding(populated_grid_client: AsyncClient) -> None:
    async with populated_grid_client as client:
        response = await client.get("/grid")
    assert response.headers["content-encoding"] == "gzip"


async def test_get_grid_body_is_valid_binary(populated_grid_client: AsyncClient) -> None:
    async with populated_grid_client as client:
        response = await client.get("/grid")
    rt = Grid.from_bytes(response.content)
    assert rt.get(0) is not None


async def test_get_grid_etag_matches_last_block(populated_grid_client: AsyncClient) -> None:
    async with populated_grid_client as client:
        response = await client.get("/grid")
    assert response.headers["etag"] == '"42.5"'


async def test_get_grid_304_on_matching_etag(populated_grid_client: AsyncClient) -> None:
    async with populated_grid_client as client:
        response = await client.get("/grid", headers={"if-none-match": '"42.5"'})
    assert response.status_code == 304
    assert response.content == b""


async def test_get_grid_200_on_stale_etag(populated_grid_client: AsyncClient) -> None:
    async with populated_grid_client as client:
        response = await client.get("/grid", headers={"if-none-match": '"41.5"'})
    assert response.status_code == 200


async def test_get_grid_cache_not_recomputed_between_requests(
    populated_grid_client: AsyncClient,
) -> None:
    async with populated_grid_client as client:
        r1 = await client.get("/grid")
        r2 = await client.get("/grid")
    assert r1.content == r2.content
    assert app.state.grid_cache.last_block == 42
    assert app.state.grid_cache.last_log_index == 5


async def test_get_grid_cache_invalidates_for_new_log_in_same_block() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT))

    async with _make_client(grid, last_block=42, last_log_index=0) as client:
        r1 = await client.get("/grid")
        assert r1.headers["etag"] == '"42.0"'

        grid.apply(CellColorUpdated(cell_id=0, renter=ADDR_A, color=0xFF8800))
        app.state.watcher._last_log_index = 1

        r2 = await client.get("/grid", headers={"if-none-match": r1.headers["etag"]})

    assert r2.status_code == 200
    assert r2.headers["etag"] == '"42.1"'
    assert r2.content != r1.content
    cell = Grid.from_bytes(r2.content).get(0)
    assert cell is not None
    assert cell.color is not None
    assert cell.color.r == 255
    assert cell.color.g == 136
    assert cell.color.b == 0


async def test_get_grid_no_last_block_returns_empty_grid(
    empty_grid_client: AsyncClient,
) -> None:
    async with empty_grid_client as client:
        response = await client.get("/grid")
    assert response.status_code == 200
    rt = Grid.from_bytes(response.content)
    assert rt._cells == {}


# --- _watcher_from_env ---


def test_watcher_from_env_uses_start_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB3_HTTP_URL", "http://localhost:8545")
    monkeypatch.setenv("WEB3_WS_URL", "ws://localhost:8545")
    monkeypatch.setenv("START_BLOCK", "12345")
    monkeypatch.setenv("GRID_ADDRESS", "0x" + "01" * 20)
    monkeypatch.setenv("TOKEN_ADDRESS", "0x" + "02" * 20)
    monkeypatch.setenv("FAUCET_ADDRESS", "0x" + "03" * 20)

    from polyplace_watcher.app import _watcher_from_env
    watcher = _watcher_from_env()
    assert watcher._start_block == 12345


# --- snapshot loop ---


class _FakeSnapshotWatcher:
    def __init__(self, last_block: int | None) -> None:
        self.grid = Grid()
        self._last_block = last_block
        self.saved_paths: list[Path] = []

    def save_snapshot(self, path: Path) -> None:
        self.saved_paths.append(path)

    async def watch(self) -> None:
        await asyncio.sleep(10_000)


async def test_snapshot_loop_saves_when_last_block_set(tmp_path: Path) -> None:
    watcher = _FakeSnapshotWatcher(last_block=42)
    path = tmp_path / "snap.json"

    task = asyncio.create_task(_snapshot_loop(watcher, path, interval=0))
    await asyncio.sleep(0.05)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert path in watcher.saved_paths


async def test_snapshot_loop_skips_when_no_last_block(tmp_path: Path) -> None:
    watcher = _FakeSnapshotWatcher(last_block=None)
    path = tmp_path / "snap.json"

    task = asyncio.create_task(_snapshot_loop(watcher, path, interval=0))
    await asyncio.sleep(0.05)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert watcher.saved_paths == []


# --- lifespan snapshot behaviour ---


async def test_lifespan_loads_existing_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snap_path = tmp_path / "snap.json"
    Snapshot(last_block=99, cells={}).model_dump_json()
    snap_path.write_text(Snapshot(last_block=99, cells={}).model_dump_json())

    loaded_paths: list[Path] = []

    class FakeWatcher(_FakeSnapshotWatcher):
        def __init__(self) -> None:
            super().__init__(last_block=None)

        def load_snapshot(self, path: Path) -> None:
            loaded_paths.append(path)
            self._last_block = 99

    monkeypatch.setenv("SNAPSHOT_PATH", str(snap_path))
    monkeypatch.setenv("SNAPSHOT_INTERVAL", "3600")
    monkeypatch.setattr(app_module, "_watcher_from_env", lambda: FakeWatcher())

    async with lifespan(app):
        assert app.state.watcher._last_block == 99
        assert loaded_paths == [snap_path]


async def test_lifespan_skips_load_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snap_path = tmp_path / "snap.json"  # does not exist

    loaded_paths: list[Path] = []

    class FakeWatcher(_FakeSnapshotWatcher):
        def __init__(self) -> None:
            super().__init__(last_block=None)

        def load_snapshot(self, path: Path) -> None:
            loaded_paths.append(path)

    monkeypatch.setenv("SNAPSHOT_PATH", str(snap_path))
    monkeypatch.setenv("SNAPSHOT_INTERVAL", "3600")
    monkeypatch.setattr(app_module, "_watcher_from_env", lambda: FakeWatcher())

    async with lifespan(app):
        assert loaded_paths == []


async def test_lifespan_saves_snapshot_on_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snap_path = tmp_path / "snap.json"

    class FakeWatcher(_FakeSnapshotWatcher):
        def __init__(self) -> None:
            super().__init__(last_block=42)

    monkeypatch.setenv("SNAPSHOT_PATH", str(snap_path))
    monkeypatch.setenv("SNAPSHOT_INTERVAL", "3600")
    watcher = FakeWatcher()
    monkeypatch.setattr(app_module, "_watcher_from_env", lambda: watcher)

    async with lifespan(app):
        pass

    assert snap_path in watcher.saved_paths
