"""Deploy contracts to a local anvil node and print env vars for the watcher."""

import os
import time

from web3 import Web3

from polyplace_contracts import deploy

ANVIL_URL = os.environ.get("WEB3_HTTP_URL", "http://127.0.0.1:8545")
DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

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

print("Export these env vars before starting the watcher:\n")
print(f"export WEB3_HTTP_URL={ANVIL_URL}")
print(f"export WEB3_WS_URL={ANVIL_URL.replace('http', 'ws')}")
print( "export START_BLOCK=0")
print(f"export GRID_ADDRESS={deployment.grid}")
print(f"export TOKEN_ADDRESS={deployment.token}")
print(f"export FAUCET_ADDRESS={deployment.faucet}")
