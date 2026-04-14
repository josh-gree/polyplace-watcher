import asyncio
from contextlib import suppress
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

from polyplace_contracts import PLACE_FAUCET_ABI, PLACE_GRID_ABI, PLACE_TOKEN_ABI
from polyplace_contracts.deploy import DEPLOY_RENT_PRICE, Deployment
from polyplace_watcher.events import CellColorUpdated, RGB
import polyplace_watcher.watcher as watcher_module
from polyplace_watcher.watcher import Watcher

from conftest import _DEPLOYER_KEY, send_tx


def test_watcher_instantiation(http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    assert watcher.grid is not None


def test_watcher_grid_initially_empty(http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    assert watcher.grid.get(0) is None


def test_backfill_populates_grid(w3: Web3, http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    caller = Account.from_key(_DEPLOYER_KEY)

    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, DEPLOY_RENT_PRICE), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    send_tx(w3, grid.functions.rentCell(5, 10, 0xFF8800), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher.backfill(from_block)

    cell_id = 10 * 1000 + 5
    cell = watcher.grid.get(cell_id)
    assert cell is not None
    assert cell.renter == caller.address.lower()
    assert cell.color == RGB(r=255, g=136, b=0)


def test_backfill_empty_range_leaves_grid_empty(w3: Web3, http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    from_block = w3.eth.block_number
    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher.backfill(from_block)
    assert watcher.grid.get(0) is None


def test_backfill_sets_last_block(w3: Web3, http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, DEPLOY_RENT_PRICE), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    receipt = send_tx(w3, grid.functions.rentCell(1, 1, 0xFF0000), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    assert watcher._last_block is None
    watcher.backfill(from_block)
    assert watcher._last_block == receipt["blockNumber"]


def test_backfill_empty_does_not_change_last_block(w3: Web3, http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher._last_block = 5
    watcher.backfill(w3.eth.block_number)
    assert watcher._last_block == 5


def test_backfill_does_not_advance_last_block_when_apply_fails(
    monkeypatch: pytest.MonkeyPatch,
    http_url: str,
    ws_url: str,
    deployed_contracts: Deployment,
) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher._last_block = 5
    monkeypatch.setattr(watcher._w3.eth, "get_logs", lambda _filter: [{"blockNumber": 6}])
    monkeypatch.setattr(
        watcher,
        "_decode_log",
        lambda _log: CellColorUpdated(cell_id=0, renter="0xabc", color=0x0000FF),
    )
    monkeypatch.setattr(watcher.grid, "apply", lambda _event: (_ for _ in ()).throw(RuntimeError("apply failed")))

    with pytest.raises(RuntimeError, match="apply failed"):
        watcher.backfill(5)

    assert watcher._last_block == 5


def test_snapshot_round_trip(w3: Web3, http_url: str, ws_url: str, deployed_contracts: Deployment, tmp_path: Path) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, DEPLOY_RENT_PRICE), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    send_tx(w3, grid.functions.rentCell(3, 4, 0x112233), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher.backfill(from_block)

    snap_path = tmp_path / "snap.json"
    watcher.save_snapshot(snap_path)

    cell_id = 4 * 1000 + 3
    watcher2 = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    assert watcher2.grid.get(cell_id) is None
    assert watcher2._last_block is None

    watcher2.load_snapshot(snap_path)
    assert watcher2._last_block == watcher._last_block
    assert watcher2.grid.get(cell_id) == watcher.grid.get(cell_id)


def test_save_snapshot_raises_without_last_block(http_url: str, ws_url: str, deployed_contracts: Deployment, tmp_path: Path) -> None:
    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    with pytest.raises(ValueError):
        watcher.save_snapshot(tmp_path / "snap.json")


async def test_watch_catches_up_from_loaded_snapshot(
    w3: Web3,
    http_url: str,
    ws_url: str,
    deployed_contracts: Deployment,
    tmp_path: Path,
) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, DEPLOY_RENT_PRICE * 3), _DEPLOYER_KEY)

    from_block = w3.eth.block_number
    send_tx(w3, grid.functions.rentCell(1, 1, 0xFF0000), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher.backfill(from_block)
    snap_path = tmp_path / "snap.json"
    watcher.save_snapshot(snap_path)

    send_tx(w3, grid.functions.rentCell(2, 2, 0x00FF00), _DEPLOYER_KEY)

    watcher2 = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher2.load_snapshot(snap_path)
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
            if watcher2.grid.get(live_cell_id) is not None:
                break
        else:
            raise AssertionError("watch did not pick up a live event after restart")

        missed_cell_id = 2 * 1000 + 2
        for _ in range(20):
            await asyncio.sleep(0.1)
            if watcher2.grid.get(missed_cell_id) is not None:
                break
        else:
            raise AssertionError("watch did not catch up from the loaded snapshot block")
    finally:
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task


async def test_watch_reconnects_after_disconnect(w3: Web3, http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    caller = Account.from_key(_DEPLOYER_KEY)

    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, DEPLOY_RENT_PRICE * 2), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watch_task = asyncio.create_task(watcher.watch())

    # wait for subscription to establish
    for _ in range(20):
        await asyncio.sleep(0.1)
        if watcher._ws_w3 is not None:
            break
    else:
        raise AssertionError("watch did not connect in time")

    # first event — picked up live by watch
    send_tx(w3, grid.functions.rentCell(1, 1, 0xFF0000), _DEPLOYER_KEY)
    cell_id_1 = 1 * 1000 + 1
    for _ in range(20):
        await asyncio.sleep(0.1)
        if watcher.grid.get(cell_id_1) is not None:
            break
    else:
        raise AssertionError("first event not picked up by watch")

    # force-disconnect the WebSocket
    await watcher._ws_w3.provider._provider_specific_disconnect()

    # second event — emitted while disconnected, recovered via backfill on reconnect
    send_tx(w3, grid.functions.rentCell(2, 2, 0x00FF00), _DEPLOYER_KEY)
    cell_id_2 = 2 * 1000 + 2
    for _ in range(30):
        await asyncio.sleep(0.1)
        if watcher.grid.get(cell_id_2) is not None:
            break
    else:
        raise AssertionError("second event not recovered after reconnect")

    watch_task.cancel()
    assert watcher.grid.get(cell_id_1) is not None
    assert watcher.grid.get(cell_id_2) is not None


async def test_watch_resubscribes_before_reconnect_backfill(
    monkeypatch: pytest.MonkeyPatch,
    http_url: str,
    ws_url: str,
    deployed_contracts: Deployment,
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

    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watcher._decode_log = lambda log: CellColorUpdated(cell_id=log["cell_id"], renter="0xabc", color=0x0000FF)  # type: ignore[method-assign]

    def backfill(_from_block: int) -> None:
        if state["subscription_active"]:
            state["race_event_delivered"] = True

    monkeypatch.setattr(watcher_module, "AsyncWeb3", FakeAsyncWeb3)
    monkeypatch.setattr(watcher, "backfill", backfill)

    watch_task = asyncio.create_task(watcher.watch())
    try:
        for _ in range(30):
            await asyncio.sleep(0.05)
            if watcher.grid.get(2) is not None:
                break
        else:
            raise AssertionError("watch did not resubscribe before reconnect backfill")
    finally:
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task


async def test_watch_populates_grid(w3: Web3, http_url: str, ws_url: str, deployed_contracts: Deployment) -> None:
    caller = Account.from_key(_DEPLOYER_KEY)

    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, DEPLOY_RENT_PRICE), _DEPLOYER_KEY)

    watcher = Watcher(http_url=http_url, ws_url=ws_url, deployment=deployed_contracts)
    watch_task = asyncio.create_task(watcher.watch())

    await asyncio.sleep(0.5)
    send_tx(w3, grid.functions.rentCell(5, 10, 0xFF8800), _DEPLOYER_KEY)

    cell_id = 10 * 1000 + 5
    for _ in range(20):
        await asyncio.sleep(0.1)
        if watcher.grid.get(cell_id) is not None:
            break
    else:
        raise AssertionError("grid was not updated within timeout")

    watch_task.cancel()

    cell = watcher.grid.get(cell_id)
    assert cell is not None
    assert cell.renter == caller.address.lower()
    assert cell.color == RGB(r=255, g=136, b=0)
