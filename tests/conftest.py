import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from eth_account import Account
from web3 import Web3
from web3.types import TxReceipt

from polyplace_watcher.forge_deploy import ForgeDeployment, deploy_via_forge

# First default anvil account private key
_DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_ANVIL_URL = "http://127.0.0.1:8545"


def send_tx(w3: Web3, fn, key: str) -> TxReceipt:
    account = Account.from_key(key)
    tx = fn.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def _rpc(w3: Web3, method: str, params: list[object]) -> object:
    response = w3.provider.make_request(method, params)  # type: ignore[attr-defined]
    if "error" in response:
        raise RuntimeError(f"{method} failed: {response['error']}")
    return response["result"]


@dataclass
class _DeploymentState:
    deployment: ForgeDeployment
    snapshot_id: object


@pytest.fixture(scope="session")
def http_url() -> str:
    return _ANVIL_URL


@pytest.fixture(scope="session")
def ws_url() -> str:
    return _ANVIL_URL.replace("http", "ws")


@pytest.fixture(scope="session")
def w3() -> Iterator[Web3]:
    proc = subprocess.Popen(
        ["anvil", "--port", "8545"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _w3 = Web3(Web3.HTTPProvider(_ANVIL_URL))
        for _ in range(20):
            if _w3.is_connected():
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("anvil did not start in time")

        yield _w3
    finally:
        proc.terminate()
        proc.wait()


@pytest.fixture(scope="session")
def _deployment_state(w3: Web3, tmp_path_factory: pytest.TempPathFactory) -> _DeploymentState:
    _rpc(w3, "anvil_reset", [])
    manifest_path = tmp_path_factory.mktemp("forge-deploy") / "deployment.json"
    deployment = deploy_via_forge(
        rpc_url=_ANVIL_URL,
        private_key=_DEPLOYER_KEY,
        manifest_path=manifest_path,
    )
    snapshot_id = _rpc(w3, "anvil_snapshot", [])
    return _DeploymentState(deployment=deployment, snapshot_id=snapshot_id)


@pytest.fixture
def deployed_contracts(w3: Web3, _deployment_state: _DeploymentState) -> ForgeDeployment:
    reverted = _rpc(w3, "anvil_revert", [_deployment_state.snapshot_id])
    if reverted is not True:
        raise RuntimeError(f"anvil_revert returned {reverted!r}")
    _deployment_state.snapshot_id = _rpc(w3, "anvil_snapshot", [])
    return _deployment_state.deployment
