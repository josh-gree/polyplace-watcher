import asyncio
from contextlib import suppress
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

from polyplace_contracts import PLACE_FAUCET_ABI, PLACE_GRID_ABI, PLACE_TOKEN_ABI
from polyplace_watcher.events import CellColorUpdated, RGB
from tools.forge_deploy import ForgeDeployment
import polyplace_watcher.watcher as watcher_module
from polyplace_watcher.watcher import Watcher

from conftest import _DEPLOYER_KEY, send_tx


def test_watcher_instantiation(http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    assert watcher.store is not None


def test_watcher_store_initially_empty(http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    assert watcher.store.get(0) is None


def test_fetch_logs_populates_store(w3: Web3, http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    caller = Account.from_key(_DEPLOYER_KEY)

    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, deployed_contracts.rent_price), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    send_tx(w3, grid.functions.rentCell(5, 10, 0xFF8800), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    for event, block, log_index in watcher.fetch_logs(from_block, w3.eth.block_number):
        watcher.store.apply(event, block, log_index)

    cell_id = 10 * 1000 + 5
    cell = watcher.store.get(cell_id)
    assert cell is not None
    assert cell.renter == caller.address.lower()
    assert cell.color == RGB(r=255, g=136, b=0)


def test_fetch_logs_empty_range_leaves_store_empty(w3: Web3, http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    from_block = w3.eth.block_number
    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    for event, block, log_index in watcher.fetch_logs(from_block, w3.eth.block_number):
        watcher.store.apply(event, block, log_index)
    assert watcher.store.get(0) is None


def test_fetch_logs_sets_last_block(w3: Web3, http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, deployed_contracts.rent_price), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    receipt = send_tx(w3, grid.functions.rentCell(1, 1, 0xFF0000), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    assert watcher.store.last_block is None
    for event, block, log_index in watcher.fetch_logs(from_block, w3.eth.block_number):
        watcher.store.apply(event, block, log_index)
    assert watcher.store.last_block == receipt["blockNumber"]


def test_fetch_logs_empty_does_not_change_last_block(w3: Web3, http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    watcher.store._last_block = 5
    head = w3.eth.block_number
    for event, block, log_index in watcher.fetch_logs(head, head):
        watcher.store.apply(event, block, log_index)
    assert watcher.store.last_block == 5


def test_watcher_rejects_non_positive_chunk_size(http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    with pytest.raises(ValueError):
        Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts, backfill_chunk_size=0)


async def test_backfill_chunks_range_across_boundaries(
    http_url: str, ws_url: str, deployed_contracts: ForgeDeployment
) -> None:
    calls: list[tuple[int, int]] = []
    watcher = Watcher(
        http_url=http_url, ws_url=ws_url, contracts=deployed_contracts,
        backfill_chunk_size=3,
    )
    watcher._current_head = lambda: 25  # type: ignore[method-assign]
    watcher.fetch_logs = lambda fb, tb: (calls.append((fb, tb)) or [])  # type: ignore[method-assign]

    async for _ in watcher._backfill(from_block=10):
        pass

    assert calls == [(10, 12), (13, 15), (16, 18), (19, 21), (22, 24), (25, 25)]


async def test_backfill_final_chunk_clipped_to_head(
    http_url: str, ws_url: str, deployed_contracts: ForgeDeployment
) -> None:
    calls: list[tuple[int, int]] = []
    watcher = Watcher(
        http_url=http_url, ws_url=ws_url, contracts=deployed_contracts,
        backfill_chunk_size=10,
    )
    watcher._current_head = lambda: 15  # type: ignore[method-assign]
    watcher.fetch_logs = lambda fb, tb: (calls.append((fb, tb)) or [])  # type: ignore[method-assign]

    async for _ in watcher._backfill(from_block=0):
        pass

    assert calls == [(0, 9), (10, 15)]


async def test_backfill_empty_range_makes_no_calls(
    http_url: str, ws_url: str, deployed_contracts: ForgeDeployment
) -> None:
    calls: list[tuple[int, int]] = []
    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    watcher._current_head = lambda: 5  # type: ignore[method-assign]
    watcher.fetch_logs = lambda fb, tb: (calls.append((fb, tb)) or [])  # type: ignore[method-assign]

    async for _ in watcher._backfill(from_block=10):
        pass

    assert calls == []


async def test_snapshot_round_trip(w3: Web3, http_url: str, ws_url: str, deployed_contracts: ForgeDeployment, tmp_path: Path) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, deployed_contracts.rent_price), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    send_tx(w3, grid.functions.rentCell(3, 4, 0x112233), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    for event, block, log_index in watcher.fetch_logs(from_block, w3.eth.block_number):
        watcher.store.apply(event, block, log_index)

    snap_path = tmp_path / "snap.json"
    await watcher.store.save_snapshot(snap_path)

    cell_id = 4 * 1000 + 3
    watcher2 = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    assert watcher2.store.get(cell_id) is None
    assert watcher2.store.last_block is None

    watcher2.store.load_snapshot(snap_path)
    assert watcher2.store.last_block == watcher.store.last_block
    assert watcher2.store.get(cell_id) == watcher.store.get(cell_id)


async def test_save_snapshot_raises_without_last_block(http_url: str, ws_url: str, deployed_contracts: ForgeDeployment, tmp_path: Path) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    with pytest.raises(ValueError):
        await watcher.store.save_snapshot(tmp_path / "snap.json")


async def test_watch_catches_up_from_loaded_snapshot(
    w3: Web3,
    http_url: str,
    ws_url: str,
    deployed_contracts: ForgeDeployment,
    tmp_path: Path,
) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, deployed_contracts.rent_price * 3), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    send_tx(w3, grid.functions.rentCell(1, 1, 0xFF0000), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    for event, block, log_index in watcher.fetch_logs(from_block, w3.eth.block_number):
        watcher.store.apply(event, block, log_index)
    snap_path = tmp_path / "snap.json"
    await watcher.store.save_snapshot(snap_path)

    send_tx(w3, grid.functions.rentCell(2, 2, 0x00FF00), _DEPLOYER_KEY)

    watcher2 = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    watcher2.store.load_snapshot(snap_path)
    watch_task = asyncio.create_task(watcher2.watch())

    try:
        for _ in range(20):
            await asyncio.sleep(0.1)
            if watcher2._ws_w3 is not None:
                break
        else:
            raise AssertionError("watch did not connect in time")

        send_tx(w3, grid.functions.rentCell(3, 3, 0x0000FF), _DEPLOYER_KEY)
        live_cell_id = 3 * 1000 + 3
        for _ in range(20):
            await asyncio.sleep(0.1)
            if watcher2.store.get(live_cell_id) is not None:
                break
        else:
            raise AssertionError("watch did not pick up a live event after restart")

        missed_cell_id = 2 * 1000 + 2
        for _ in range(20):
            await asyncio.sleep(0.1)
            if watcher2.store.get(missed_cell_id) is not None:
                break
        else:
            raise AssertionError("watch did not catch up from the loaded snapshot block")
    finally:
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task


async def test_watch_reconnects_after_disconnect(w3: Web3, http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    caller = Account.from_key(_DEPLOYER_KEY)

    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, deployed_contracts.rent_price * 2), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    watch_task = asyncio.create_task(watcher.watch())

    for _ in range(20):
        await asyncio.sleep(0.1)
        if watcher._ws_w3 is not None:
            break
    else:
        raise AssertionError("watch did not connect in time")

    send_tx(w3, grid.functions.rentCell(1, 1, 0xFF0000), _DEPLOYER_KEY)
    cell_id_1 = 1 * 1000 + 1
    for _ in range(20):
        await asyncio.sleep(0.1)
        if watcher.store.get(cell_id_1) is not None:
            break
    else:
        raise AssertionError("first event not picked up by watch")

    await watcher._ws_w3.provider._provider_specific_disconnect()

    send_tx(w3, grid.functions.rentCell(2, 2, 0x00FF00), _DEPLOYER_KEY)
    cell_id_2 = 2 * 1000 + 2
    for _ in range(30):
        await asyncio.sleep(0.1)
        if watcher.store.get(cell_id_2) is not None:
            break
    else:
        raise AssertionError("second event not recovered after reconnect")

    watch_task.cancel()
    assert watcher.store.get(cell_id_1) is not None
    assert watcher.store.get(cell_id_2) is not None


async def test_watch_resubscribes_before_reconnect_backfill(
    monkeypatch: pytest.MonkeyPatch,
    http_url: str,
    ws_url: str,
    deployed_contracts: ForgeDeployment,
) -> None:
    state = {
        "connection_count": 0,
        "subscription_active": False,
        "race_event_delivered": False,
    }

    class FakeEth:
        async def subscribe(self, *_args: object, **_kwargs: object) -> None:
            state["subscription_active"] = True

    class FakeSocket:
        def __init__(self, connection_id: int) -> None:
            self.connection_id = connection_id

        async def process_subscriptions(self):
            if self.connection_id == 1:
                yield {"result": {"blockNumber": 100, "logIndex": 0, "cell_id": 1}}
                raise watcher_module.PersistentConnectionError()

            for _ in range(20):
                await asyncio.sleep(0.01)
                if state["race_event_delivered"]:
                    yield {"result": {"blockNumber": 101, "logIndex": 0, "cell_id": 2}}
                    return

    class FakeAsyncWeb3:
        eth = FakeEth()

        @staticmethod
        def WebSocketProvider(url: str) -> str:
            return url

        def __init__(self, _provider: str) -> None:
            state["connection_count"] += 1
            self.socket = FakeSocket(state["connection_count"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            state["subscription_active"] = False

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    watcher._decode_log = lambda log: CellColorUpdated(cell_id=log["cell_id"], renter="0xabc", color=0x0000FF)  # type: ignore[method-assign]

    def fetch_logs(_from_block: int, _to_block: int) -> list:
        if state["subscription_active"]:
            state["race_event_delivered"] = True
        return []

    monkeypatch.setattr(watcher_module, "AsyncWeb3", FakeAsyncWeb3)
    monkeypatch.setattr(watcher, "fetch_logs", fetch_logs)
    monkeypatch.setattr(watcher, "_current_head", lambda: 0)

    watch_task = asyncio.create_task(watcher.watch())
    try:
        for _ in range(30):
            await asyncio.sleep(0.05)
            if watcher.store.get(2) is not None:
                break
        else:
            raise AssertionError("watch did not resubscribe before reconnect backfill")
    finally:
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task


async def test_watch_backfills_from_start_block_on_cold_start(
    monkeypatch: pytest.MonkeyPatch,
    http_url: str,
    ws_url: str,
    deployed_contracts: ForgeDeployment,
) -> None:
    state: dict[str, object] = {"backfill_from_block": None}

    class FakeEth:
        async def subscribe(self, *_args: object, **_kwargs: object) -> None:
            await asyncio.sleep(0)

    class FakeSocket:
        async def process_subscriptions(self):
            yield {"result": {"blockNumber": 1, "logIndex": 0, "cell_id": 1}}

    class FakeAsyncWeb3:
        eth = FakeEth()

        @staticmethod
        def WebSocketProvider(url: str) -> str:
            return url

        def __init__(self, _provider: str) -> None:
            self.socket = FakeSocket()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts, start_block=50)
    watcher._decode_log = lambda log: CellColorUpdated(cell_id=log["cell_id"], renter="0xabc", color=0x0000FF)  # type: ignore[method-assign]

    def fetch_logs(from_block: int, _to_block: int) -> list:
        if state["backfill_from_block"] is None:
            state["backfill_from_block"] = from_block
        return []

    monkeypatch.setattr(watcher_module, "AsyncWeb3", FakeAsyncWeb3)
    monkeypatch.setattr(watcher, "fetch_logs", fetch_logs)
    monkeypatch.setattr(watcher, "_current_head", lambda: 50)

    watch_task = asyncio.create_task(watcher.watch())
    try:
        await asyncio.sleep(0.1)
    finally:
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task

    assert state["backfill_from_block"] == 50


async def test_watch_backfills_from_last_block_on_reconnect(
    monkeypatch: pytest.MonkeyPatch,
    http_url: str,
    ws_url: str,
    deployed_contracts: ForgeDeployment,
) -> None:
    state: dict[str, object] = {"backfill_from_block": None}

    class FakeEth:
        async def subscribe(self, *_args: object, **_kwargs: object) -> None:
            await asyncio.sleep(0)

    class FakeSocket:
        async def process_subscriptions(self):
            yield {"result": {"blockNumber": 101, "logIndex": 0, "cell_id": 1}}

    class FakeAsyncWeb3:
        eth = FakeEth()

        @staticmethod
        def WebSocketProvider(url: str) -> str:
            return url

        def __init__(self, _provider: str) -> None:
            self.socket = FakeSocket()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts, start_block=50)
    watcher.store._last_block = 100
    watcher._decode_log = lambda log: CellColorUpdated(cell_id=log["cell_id"], renter="0xabc", color=0x0000FF)  # type: ignore[method-assign]

    def fetch_logs(from_block: int, _to_block: int) -> list:
        if state["backfill_from_block"] is None:
            state["backfill_from_block"] = from_block
        return []

    monkeypatch.setattr(watcher_module, "AsyncWeb3", FakeAsyncWeb3)
    monkeypatch.setattr(watcher, "fetch_logs", fetch_logs)
    monkeypatch.setattr(watcher, "_current_head", lambda: 100)

    watch_task = asyncio.create_task(watcher.watch())
    try:
        await asyncio.sleep(0.1)
    finally:
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task

    assert state["backfill_from_block"] == 100


async def test_watch_fetch_logs_called_via_to_thread(
    monkeypatch: pytest.MonkeyPatch,
    http_url: str,
    ws_url: str,
    deployed_contracts: ForgeDeployment,
) -> None:
    to_thread_calls: list[object] = []

    class FakeEth:
        async def subscribe(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FakeSocket:
        async def process_subscriptions(self):
            await asyncio.sleep(10_000)
            return
            yield

    class FakeAsyncWeb3:
        eth = FakeEth()

        @staticmethod
        def WebSocketProvider(url: str) -> str:
            return url

        def __init__(self, _provider: str) -> None:
            self.socket = FakeSocket()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

    def fake_fetch_logs(_from_block: int, _to_block: int) -> list:
        return []

    async def fake_to_thread(func: object, *args: object, **kwargs: object) -> object:
        to_thread_calls.append(func)
        if callable(func):
            return func(*args, **kwargs)  # type: ignore[operator]

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    monkeypatch.setattr(watcher_module, "AsyncWeb3", FakeAsyncWeb3)
    monkeypatch.setattr(watcher, "fetch_logs", fake_fetch_logs)
    monkeypatch.setattr(watcher, "_current_head", lambda: 0)
    monkeypatch.setattr(watcher_module.asyncio, "to_thread", fake_to_thread)

    watch_task = asyncio.create_task(watcher.watch())
    try:
        await asyncio.sleep(0.1)
    finally:
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task

    assert fake_fetch_logs in to_thread_calls


async def test_watch_populates_store(w3: Web3, http_url: str, ws_url: str, deployed_contracts: ForgeDeployment) -> None:
    caller = Account.from_key(_DEPLOYER_KEY)

    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, deployed_contracts.rent_price), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, contracts=deployed_contracts)
    watch_task = asyncio.create_task(watcher.watch())

    await asyncio.sleep(0.5)
    send_tx(w3, grid.functions.rentCell(5, 10, 0xFF8800), _DEPLOYER_KEY)

    cell_id = 10 * 1000 + 5
    for _ in range(20):
        await asyncio.sleep(0.1)
        if watcher.store.get(cell_id) is not None:
            break
    else:
        raise AssertionError("store was not updated within timeout")

    watch_task.cancel()

    cell = watcher.store.get(cell_id)
    assert cell is not None
    assert cell.renter == caller.address.lower()
    assert cell.color == RGB(r=255, g=136, b=0)
