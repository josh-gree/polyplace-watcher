"""Fixtures for the chain-backed spec tests.

Everything here is infrastructure for exercising the spec through real
channels: a private anvil chain, the real contract suite deployed onto it,
a renter that sends real transactions, the watcher run as a real subprocess
(the same `uvicorn polyplace_watcher.app:app` the deploy flows use), and a
severable TCP proxy for simulating chain-WS outages.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from eth_account import Account
from web3 import Web3

from polyplace_contracts import PLACE_FAUCET_ABI, PLACE_GRID_ABI, PLACE_TOKEN_ABI
from polyplace_contracts.deploy import DeployParams, deploy

REPO_ROOT = Path(__file__).resolve().parents[1]

# Anvil's default-mnemonic accounts 0 and 1 — well-known throwaway keys.
DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
RENTER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

ALLOWED_ORIGIN = "http://spec-test.example"
GIT_SHA = "spec-test-sha"



def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: needs anvil on PATH")


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if shutil.which("anvil"):
        return
    skip = pytest.mark.skip(reason="anvil not on PATH")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(predicate, timeout: float, interval: float = 0.05, message: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for {message}")


# --- chain -----------------------------------------------------------------


@dataclass
class Chain:
    process: subprocess.Popen
    port: int

    @property
    def http_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


@pytest.fixture(scope="session")
def chain() -> Chain:
    port = free_port()
    process = subprocess.Popen(
        ["anvil", "--port", str(port), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    c = Chain(process=process, port=port)
    w3 = Web3(Web3.HTTPProvider(c.http_url))

    def _up():
        try:
            return w3.is_connected()
        except Exception:
            return False

    wait_until(_up, timeout=15, message="anvil RPC")
    yield c
    os.killpg(os.getpgid(process.pid), signal.SIGKILL)


@pytest.fixture(scope="session")
def w3(chain: Chain) -> Web3:
    return Web3(Web3.HTTPProvider(chain.http_url))


@pytest.fixture(scope="session")
def deployment(w3: Web3):
    # A huge claim_amount so one faucet claim funds every rent in the suite.
    return deploy(w3, DEPLOYER_KEY, DeployParams(claim_amount=10_000_000 * 10**18))


def _packed(color: tuple[int, int, int]) -> int:
    r, g, b = color
    return (r << 16) | (g << 8) | b


class Wallet:
    """Sends real transactions from one anvil account."""

    def __init__(self, w3: Web3, deployment, key: str) -> None:
        self.w3 = w3
        self.account = Account.from_key(key)
        self.address = self.account.address
        self.token = w3.eth.contract(address=deployment.token, abi=PLACE_TOKEN_ABI)
        self.faucet = w3.eth.contract(address=deployment.faucet, abi=PLACE_FAUCET_ABI)
        self.grid = w3.eth.contract(address=deployment.grid, abi=PLACE_GRID_ABI)

    def send(self, fn) -> dict:
        tx = fn.build_transaction(
            {
                "from": self.address,
                "nonce": self.w3.eth.get_transaction_count(self.address),
                "chainId": self.w3.eth.chain_id,
            }
        )
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        assert receipt["status"] == 1, "transaction reverted"
        return receipt

    def fund(self) -> None:
        self.send(self.faucet.functions.claim())
        self.send(self.token.functions.approve(self.grid.address, 2**256 - 1))

    def rent(self, x: int, y: int, color: tuple[int, int, int]) -> dict:
        """Rent one cell (emits CellRented + CellColorUpdated in one tx)."""
        return self.send(self.grid.functions.bulkRentCells([x], [y], [_packed(color)]))

    def set_color(self, x: int, y: int, color: tuple[int, int, int]) -> dict:
        """Recolor an actively rented cell (emits CellColorUpdated alone)."""
        return self.send(self.grid.functions.setColor(x, y, _packed(color)))


@pytest.fixture(scope="session")
def renter(w3: Web3, deployment) -> Wallet:
    wallet = Wallet(w3, deployment, RENTER_KEY)
    wallet.fund()
    return wallet


@pytest.fixture(scope="session")
def owner(w3: Web3, deployment) -> Wallet:
    """The contract owner (deployer) — can emit the admin events the
    watcher must ignore (SPEC §3)."""
    return Wallet(w3, deployment, DEPLOYER_KEY)


# --- the watcher as a subprocess --------------------------------------------


class Service:
    """The watcher run exactly as the deploy flows run it."""

    def __init__(self, env: dict[str, str], snapshot_dir: Path) -> None:
        self.port = free_port()
        self.snapshot_dir = snapshot_dir
        self.env = env
        self.process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    def snapshot_path(self) -> Path:
        return self.snapshot_dir / f"snapshot-{self.env['GRID_ADDRESS'].lower()}.json"

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                "uv", "run", "uvicorn", "polyplace_watcher.app:app",
                "--host", "127.0.0.1", "--port", str(self.port),
            ],
            cwd=REPO_ROOT,
            env={**os.environ, **self.env},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def health(self) -> dict | None:
        try:
            response = httpx.get(f"{self.url}/health", timeout=2)
        except httpx.HTTPError:
            return None
        return response.json() if response.status_code == 200 else None

    def wait_healthy(self, timeout: float = 30) -> None:
        wait_until(self.health, timeout=timeout, message="watcher /health")

    def wait_for_block(self, block: int, timeout: float = 30) -> None:
        """Wait until the watcher has applied an event at or past `block`."""

        def _caught_up():
            health = self.health()
            return health and health["last_block"] is not None and health["last_block"] >= block

        wait_until(_caught_up, timeout=timeout, message=f"last_block >= {block}")

    def stop(self) -> None:
        """Graceful shutdown (SIGTERM) — per SPEC §4 this saves the snapshot."""
        if self.process is None:
            return
        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        self.process.wait(timeout=20)
        self.process = None

    def kill(self) -> None:
        if self.process is None:
            return
        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        self.process.wait(timeout=20)
        self.process = None


@pytest.fixture
def make_service(chain: Chain, deployment, tmp_path: Path):
    """Factory for watcher services; every instance is cleaned up."""
    services: list[Service] = []

    def _make(ws_url: str | None = None, http_url: str | None = None, start: bool = True) -> Service:
        service = Service(
            env={
                "WEB3_HTTP_URL": http_url or chain.http_url,
                "WEB3_WS_URL": ws_url or chain.ws_url,
                "GRID_ADDRESS": deployment.grid,
                "START_BLOCK": str(deployment.start_block),
                "CORS_ORIGINS": ALLOWED_ORIGIN,
                "SNAPSHOT_DIR": str(tmp_path),
                "POLYPLACE_GIT_SHA": GIT_SHA,
            },
            snapshot_dir=tmp_path,
        )
        services.append(service)
        if start:
            service.start()
            service.wait_healthy()
        return service

    yield _make
    for service in services:
        service.kill()


# --- severable TCP proxy (for chain-WS outage tests) -------------------------


class TcpProxy:
    """Forwards 127.0.0.1:{port} -> target; sever() drops and refuses traffic."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.port = free_port()
        self._severed = threading.Event()
        self._stopped = threading.Event()
        self._conns: list[socket.socket] = []
        self._lock = threading.Lock()
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", self.port))
        self._listener.listen()
        self._listener.settimeout(0.2)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def _accept_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if self._severed.is_set():
                client.close()
                continue
            try:
                upstream = socket.create_connection(self.target)
            except OSError:
                client.close()
                continue
            with self._lock:
                self._conns += [client, upstream]
            threading.Thread(target=self._pump, args=(client, upstream), daemon=True).start()

    def _pump(self, a: socket.socket, b: socket.socket) -> None:
        sockets = [a, b]
        try:
            while not self._severed.is_set() and not self._stopped.is_set():
                readable, _, _ = select.select(sockets, [], [], 0.2)
                for src in readable:
                    data = src.recv(65536)
                    if not data:
                        return
                    (b if src is a else a).sendall(data)
        except OSError:
            pass
        finally:
            for s in sockets:
                try:
                    s.close()
                except OSError:
                    pass

    def sever(self) -> None:
        """Hard-drop every connection and refuse new ones until resume()."""
        self._severed.set()
        with self._lock:
            for s in self._conns:
                try:
                    s.close()
                except OSError:
                    pass
            self._conns.clear()

    def resume(self) -> None:
        self._severed.clear()

    def close(self) -> None:
        self._stopped.set()
        self.sever()
        self._listener.close()


@pytest.fixture
def ws_proxy(chain: Chain):
    proxy = TcpProxy("127.0.0.1", chain.port)
    yield proxy
    proxy.close()


# --- shared helpers ----------------------------------------------------------


def cell_state(cell) -> tuple:
    """Comparable view of a decoded cell: (renter, expires_at, color)."""
    return (cell.renter, cell.expires_at, cell.color)


def read_snapshot(path: Path) -> dict:
    return json.loads(path.read_text())
