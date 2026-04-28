from web3 import Web3

from polyplace_contracts import (
    INITIAL_SUPPLY,
    PLACE_FAUCET_ABI,
    PLACE_GRID_ABI,
    PLACE_TOKEN_ABI,
)
from polyplace_contracts.deploy import Deployment


def test_token_metadata(w3: Web3, deployed_contracts: Deployment) -> None:
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    assert token.functions.name().call() == "Place"
    assert token.functions.symbol().call() == "PLACE"
    assert token.functions.decimals().call() == 18
    assert token.functions.totalSupply().call() == INITIAL_SUPPLY


def test_faucet_config(w3: Web3, deployed_contracts: Deployment) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    assert faucet.functions.TOKEN().call() == deployed_contracts.token
    assert faucet.functions.claimAmount().call() == deployed_contracts.params.claim_amount
    assert faucet.functions.cooldown().call() == deployed_contracts.params.cooldown


def test_grid_config(w3: Web3, deployed_contracts: Deployment) -> None:
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)
    assert grid.functions.TOKEN().call() == deployed_contracts.token
    assert grid.functions.FAUCET().call() == deployed_contracts.faucet
    assert grid.functions.rentPrice().call() == deployed_contracts.params.rent_price
    assert grid.functions.rentDuration().call() == deployed_contracts.params.rent_duration
