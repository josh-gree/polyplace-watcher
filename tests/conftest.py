import subprocess
import time
from collections.abc import Iterator

import pytest
from web3 import Web3

from polyplace_contracts import deploy
from polyplace_contracts.deploy import Deployment

# First default anvil account private key
_DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_ANVIL_URL = "http://127.0.0.1:8545"


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


@pytest.fixture
def deployed_contracts(w3: Web3) -> Deployment:
    w3.provider.make_request("anvil_reset", [])  # type: ignore[attr-defined]
    return deploy(w3, _DEPLOYER_KEY)
