"""Paint random colours to random cells, 10 per block, until the full grid is filled."""

import os
import random
import time

from eth_account import Account
from web3 import Web3

from polyplace_contracts import PLACE_FAUCET_ABI, PLACE_GRID_ABI, PLACE_TOKEN_ABI

ANVIL_URL = os.environ.get("WEB3_HTTP_URL", "http://127.0.0.1:8545")
DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
GRID_SIZE = 1000
BATCH_SIZE = 1000  # transactions per block
BLOCK_TIME = 2    # seconds between batches — match anvil --block-time

grid_address = os.environ["GRID_ADDRESS"]
token_address = os.environ["TOKEN_ADDRESS"]
faucet_address = os.environ["FAUCET_ADDRESS"]

w3 = Web3(Web3.HTTPProvider(ANVIL_URL))
account = Account.from_key(DEPLOYER_KEY)

faucet = w3.eth.contract(address=faucet_address, abi=PLACE_FAUCET_ABI)
token = w3.eth.contract(address=token_address, abi=PLACE_TOKEN_ABI)
grid = w3.eth.contract(address=grid_address, abi=PLACE_GRID_ABI)


def send(fn) -> None:
    tx = fn.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)


total_cells = GRID_SIZE * GRID_SIZE
rent_price = grid.functions.rentPrice().call()
total_cost = total_cells * rent_price
total_blocks = total_cells // BATCH_SIZE
eta_seconds = total_blocks * BLOCK_TIME

print(f"Grid:       {GRID_SIZE}×{GRID_SIZE} = {total_cells:,} cells")
print(f"Batch size: {BATCH_SIZE} tx/block  |  block time: {BLOCK_TIME}s")
print(f"Cost:       {total_cost // 10**18:,} tokens")
print(f"ETA:        ~{eta_seconds // 3600}h {(eta_seconds % 3600) // 60}m")
print()

print("Funding account via faucet...")
send(faucet.functions.setClaimAmount(total_cost))
send(faucet.functions.claim())

print("Approving grid to spend tokens...")
send(token.functions.approve(grid_address, total_cost))

# Estimate gas once (add buffer for state-dependent SSTORE cost variation)
gas = grid.functions.rentCell(0, 0, 0xFF0000).estimate_gas({"from": account.address}) + 10_000

# Shuffle all cell IDs so we visit them in a random order
cells = list(range(total_cells))
random.shuffle(cells)

nonce = w3.eth.get_transaction_count(account.address)
last_tx_hash = None
t0 = time.monotonic()

print(f"Painting {total_cells:,} cells in batches of {BATCH_SIZE}...")
for batch_num, batch_start in enumerate(range(0, total_cells, BATCH_SIZE)):
    batch = cells[batch_start : batch_start + BATCH_SIZE]

    for cell_id in batch:
        x = cell_id % GRID_SIZE
        y = cell_id // GRID_SIZE
        color = random.randint(0, 0xFFFFFF)
        tx = grid.functions.rentCell(x, y, color).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": gas,
        })
        last_tx_hash = w3.eth.send_raw_transaction(account.sign_transaction(tx).raw_transaction)
        nonce += 1

    cells_done = min(batch_start + BATCH_SIZE, total_cells)
    elapsed = time.monotonic() - t0
    print(f"  batch {batch_num + 1:,}/{total_blocks:,}  —  {cells_done:,}/{total_cells:,} cells  ({elapsed:.0f}s elapsed)")

    if cells_done < total_cells:
        time.sleep(BLOCK_TIME)

print("Waiting for final transaction...")
w3.eth.wait_for_transaction_receipt(last_tx_hash)
print("Done — full grid painted.")
