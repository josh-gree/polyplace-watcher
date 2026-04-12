from web3 import Web3

from polyplace_contracts import (
    PLACE_FAUCET_ABI,
    PLACE_GRID_ABI,
    PLACE_TOKEN_ABI,
)
from polyplace_contracts.deploy import (
    DEPLOY_CLAIM_AMOUNT,
    DEPLOY_COOLDOWN,
    DEPLOY_RENT_DURATION,
    DEPLOY_RENT_PRICE,
    INITIAL_SUPPLY,
    Deployment,
)


def test_token_metadata(w3: Web3, deployed_contracts: Deployment) -> None:
    token = w3.eth.contract(address=deployed_contracts.token, abi=PLACE_TOKEN_ABI)
    assert token.functions.name().call() == "Place"
    assert token.functions.symbol().call() == "PLACE"
    assert token.functions.decimals().call() == 18
    assert token.functions.totalSupply().call() == INITIAL_SUPPLY


def test_faucet_config(w3: Web3, deployed_contracts: Deployment) -> None:
    faucet = w3.eth.contract(address=deployed_contracts.faucet, abi=PLACE_FAUCET_ABI)
    assert faucet.functions.TOKEN().call() == deployed_contracts.token
    assert faucet.functions.claimAmount().call() == DEPLOY_CLAIM_AMOUNT
    assert faucet.functions.cooldown().call() == DEPLOY_COOLDOWN


def test_grid_config(w3: Web3, deployed_contracts: Deployment) -> None:
    grid = w3.eth.contract(address=deployed_contracts.grid, abi=PLACE_GRID_ABI)
    assert grid.functions.TOKEN().call() == deployed_contracts.token
    assert grid.functions.FAUCET().call() == deployed_contracts.faucet
    assert grid.functions.rentPrice().call() == DEPLOY_RENT_PRICE
    assert grid.functions.rentDuration().call() == DEPLOY_RENT_DURATION
