import asyncio

from eth_account import Account
from httpx import ASGITransport, AsyncClient
from web3 import Web3

from polyplace_contracts import PLACE_FAUCET_ABI, PLACE_GRID_ABI, PLACE_TOKEN_ABI
from polyplace_contracts.deploy import DEPLOY_RENT_PRICE, Deployment
from polyplace_watcher.app import app, lifespan
from polyplace_watcher.events import RGB
from polyplace_watcher.grid import Grid

from conftest import _DEPLOYER_KEY, send_tx

_CALLER = Account.from_key(_DEPLOYER_KEY)


async def _wait_for(condition, *, retries: int = 30, interval: float = 0.1, msg: str) -> None:
    for _ in range(retries):
        await asyncio.sleep(interval)
        if condition():
            return
    raise AssertionError(msg)


async def test_e2e_grid_endpoint(
    monkeypatch,
    w3: Web3,
    http_url: str,
    ws_url: str,
    deployed_contracts: Deployment,
    tmp_path,
) -> None:
    monkeypatch.setenv("WEB3_HTTP_URL", http_url)
    monkeypatch.setenv("WEB3_WS_URL", ws_url)
    monkeypatch.setenv("GRID_ADDRESS", deployed_contracts.grid)
    monkeypatch.setenv("TOKEN_ADDRESS", deployed_contracts.token)
    monkeypatch.setenv("FAUCET_ADDRESS", deployed_contracts.faucet)
    monkeypatch.setenv("SNAPSHOT_PATH", str(tmp_path / "snap.json"))
    monkeypatch.setenv("SNAPSHOT_INTERVAL", "3600")
    monkeypatch.setenv("START_BLOCK", "0")

    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    grid_contract = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)

    send_tx(w3, faucet.functions.claim(), _DEPLOYER_KEY)
    send_tx(w3, token.functions.approve(deployed_contracts.grid, DEPLOY_RENT_PRICE * 2), _DEPLOYER_KEY)

    async with lifespan(app):
        store = app.state.store
        watcher = app.state.watcher

        await _wait_for(
            lambda: watcher._ws_w3 is not None,
            msg="watcher did not connect in time",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

            # --- initial state: empty grid ---
            r1 = await client.get("/grid")
            assert r1.status_code == 200
            assert Grid.from_bytes(r1.content)._cells == {}

            # --- rent cell (5, 10) with color 0xFF8800 ---
            cell_id_1 = 10 * 1000 + 5
            send_tx(w3, grid_contract.functions.rentCell(5, 10, 0xFF8800), _DEPLOYER_KEY)

            await _wait_for(
                lambda: store.get(cell_id_1) is not None,
                msg="watcher did not pick up first transaction",
            )

            r2 = await client.get("/grid")
            assert r2.status_code == 200
            assert r2.headers["etag"] != r1.headers["etag"]
            cell = Grid.from_bytes(r2.content).get(cell_id_1)
            assert cell is not None
            assert cell.renter == _CALLER.address.lower()
            assert cell.color == RGB(r=255, g=136, b=0)

            # --- 304 while grid hasn't changed ---
            r_cached = await client.get("/grid", headers={"if-none-match": r2.headers["etag"]})
            assert r_cached.status_code == 304

            # --- rent a second cell (1, 1) with color 0x112233 ---
            cell_id_2 = 1 * 1000 + 1
            send_tx(w3, grid_contract.functions.rentCell(1, 1, 0x112233), _DEPLOYER_KEY)

            await _wait_for(
                lambda: store.get(cell_id_2) is not None,
                msg="watcher did not pick up second transaction",
            )

            r3 = await client.get("/grid")
            assert r3.status_code == 200
            assert r3.headers["etag"] != r2.headers["etag"]
            grid_state = Grid.from_bytes(r3.content)
            assert grid_state.get(cell_id_1) is not None  # first cell still present
            cell2 = grid_state.get(cell_id_2)
            assert cell2 is not None
            assert cell2.renter == _CALLER.address.lower()
            assert cell2.color == RGB(r=0x11, g=0x22, b=0x33)
