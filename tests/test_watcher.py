import asyncio
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

from polyplace_contracts import PLACE_FAUCET_ABI, PLACE_GRID_ABI, PLACE_TOKEN_ABI
from polyplace_contracts.deploy import DEPLOY_RENT_PRICE, Deployment
from polyplace_watcher.events import RGB
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
    assert cell.renter == caller.address
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
    assert cell.renter == caller.address
    assert cell.color == RGB(r=255, g=136, b=0)
