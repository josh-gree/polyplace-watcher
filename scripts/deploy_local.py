"""Deploy contracts to local Anvil and write watcher runtime config."""

import os
import time
from pathlib import Path

from web3 import Web3

from polyplace_contracts import deploy

ANVIL_URL = os.environ.get("WEB3_HTTP_URL", "http://127.0.0.1:8545")
WATCHER_HTTP_URL = os.environ.get("WATCHER_WEB3_HTTP_URL", "http://anvil:8545")
WATCHER_WS_URL = os.environ.get("WATCHER_WEB3_WS_URL", "ws://anvil:8545")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://127.0.0.1:8787,http://localhost:8787")
DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHER_ENV_PATH = REPO_ROOT / ".local" / "watcher.env"

w3 = Web3(Web3.HTTPProvider(ANVIL_URL))

for _ in range(20):
    if w3.is_connected():
        break
    time.sleep(0.1)
else:
    raise SystemExit(f"Cannot connect to anvil at {ANVIL_URL} — is it running?")

print("Deploying contracts...", flush=True)
deployment = deploy(w3, DEPLOYER_KEY, cooldown=30)
print("Done.\n")

WATCHER_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
WATCHER_ENV_PATH.write_text(
    "\n".join([
        f"WEB3_HTTP_URL={WATCHER_HTTP_URL}",
        f"WEB3_WS_URL={WATCHER_WS_URL}",
        "START_BLOCK=0",
        f"GRID_ADDRESS={deployment.grid}",
        f"TOKEN_ADDRESS={deployment.token}",
        f"FAUCET_ADDRESS={deployment.faucet}",
        f"CORS_ORIGINS={CORS_ORIGINS}",
        "",
    ])
)

print(f"Wrote watcher env file: {WATCHER_ENV_PATH.relative_to(REPO_ROOT)}\n")
print("Watcher config:")
print(WATCHER_ENV_PATH.read_text())
