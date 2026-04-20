"""Shared helpers for the dev CLI: RPC, accounts, deployment file, color parsing."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values
from eth_account.signers.local import LocalAccount
from web3 import Web3

from polyplace_contracts import Deployment

DEFAULT_RPC_URL = "http://127.0.0.1:8545"
DEFAULT_ENV_PATH = Path(".local/watcher.env")

# Anvil's deterministic dev accounts (well-known, safe to hardcode for local).
NAMED_ACCOUNTS: dict[str, str] = {
    "deployer": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
}


def get_w3(rpc_url: str = DEFAULT_RPC_URL) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to RPC at {rpc_url}")
    return w3


def get_account(w3: Web3, key_or_name: str) -> LocalAccount:
    private_key = NAMED_ACCOUNTS.get(key_or_name, key_or_name)
    return w3.eth.account.from_key(private_key)


def load_addresses(path: Path = DEFAULT_ENV_PATH) -> Deployment:
    if not path.exists():
        raise RuntimeError(f"No deployment file at {path}. Run `polyplace-dev deploy` first.")
    env = dotenv_values(path)
    missing = [k for k in ("TOKEN_ADDRESS", "FAUCET_ADDRESS", "GRID_ADDRESS") if not env.get(k)]
    if missing:
        raise RuntimeError(f"Missing {', '.join(missing)} in {path}")
    return Deployment(
        token=env["TOKEN_ADDRESS"],
        faucet=env["FAUCET_ADDRESS"],
        grid=env["GRID_ADDRESS"],
    )


def parse_color(value: str) -> int:
    """Parse '#rrggbb', 'rrggbb', or '0xrrggbb' into a uint24 int."""
    s = value.strip().lower()
    if s.startswith("#"):
        s = s[1:]
    elif s.startswith("0x"):
        s = s[2:]
    if len(s) != 6:
        raise ValueError(f"color must be 6 hex digits, got {value!r}")
    return int(s, 16)
