import asyncio

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
