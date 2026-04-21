from web3 import Web3

from polyplace_contracts import (
    PLACE_FAUCET_ABI,
    PLACE_GRID_ABI,
    PLACE_TOKEN_ABI,
)
from polyplace_watcher.forge_deploy import ForgeDeployment


def test_token_metadata(w3: Web3, deployed_contracts: ForgeDeployment) -> None:
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    assert token.functions.name().call() == "Place"
    assert token.functions.symbol().call() == "PLACE"
    assert token.functions.decimals().call() == 18
    assert token.functions.totalSupply().call() == deployed_contracts.initial_supply


def test_faucet_config(w3: Web3, deployed_contracts: ForgeDeployment) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    assert faucet.functions.TOKEN().call() == deployed_contracts.token
    assert faucet.functions.claimAmount().call() == deployed_contracts.claim_amount
    assert faucet.functions.cooldown().call() == deployed_contracts.cooldown


def test_grid_config(w3: Web3, deployed_contracts: ForgeDeployment) -> None:
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)
    assert grid.functions.TOKEN().call() == deployed_contracts.token
    assert grid.functions.FAUCET().call() == deployed_contracts.faucet
    assert grid.functions.rentPrice().call() == deployed_contracts.rent_price
    assert grid.functions.rentDuration().call() == deployed_contracts.rent_duration
