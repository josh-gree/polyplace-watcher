"""Thin wrapper classes around the polyplace contracts for dev-CLI use."""

from __future__ import annotations

from typing import Any

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.types import TxReceipt

from polyplace_contracts import (
    PLACE_FAUCET_ABI,
    PLACE_GRID_ABI,
    PLACE_TOKEN_ABI,
)

from .helpers import parse_color


def _format_receipt(receipt: TxReceipt) -> dict[str, Any]:
    return {
        "tx": receipt.transactionHash.hex(),
        "status": receipt.status,
        "block": receipt.blockNumber,
        "gas_used": receipt.gasUsed,
    }


class _ContractWrapper:
    def __init__(self, w3: Web3, address: str, account: LocalAccount, abi: list[dict]) -> None:
        self._w3 = w3
        self._account = account
        self._contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)

    def _call(self, name: str, *args: Any) -> Any:
        return self._contract.functions[name](*args).call()

    def _send_fn(self, fn: ContractFunction, **tx_overrides: Any) -> dict[str, Any]:
        tx = fn.build_transaction({
            "from": self._account.address,
            "nonce": self._w3.eth.get_transaction_count(self._account.address),
            **tx_overrides,
        })
        signed = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash)
        return _format_receipt(receipt)

    def _send(self, name: str, *args: Any, **tx_overrides: Any) -> dict[str, Any]:
        return self._send_fn(self._contract.functions[name](*args), **tx_overrides)


class Faucet(_ContractWrapper):
    def __init__(self, w3: Web3, address: str, account: LocalAccount) -> None:
        super().__init__(w3, address, account, PLACE_FAUCET_ABI)

    def token(self) -> str:
        return self._call("TOKEN")

    def claim_amount(self) -> int:
        return self._call("claimAmount")

    def cooldown(self) -> int:
        return self._call("cooldown")

    def last_claimed(self, address: str) -> int:
        return self._call("lastClaimed", Web3.to_checksum_address(address))

    def owner(self) -> str:
        return self._call("owner")

    def claim(self) -> dict[str, Any]:
        return self._send("claim")

    def set_claim_amount(self, amount: int) -> dict[str, Any]:
        return self._send("setClaimAmount", amount)

    def set_cooldown(self, seconds: int) -> dict[str, Any]:
        return self._send("setCooldown", seconds)

    def transfer_ownership(self, new_owner: str) -> dict[str, Any]:
        return self._send("transferOwnership", Web3.to_checksum_address(new_owner))

    def renounce_ownership(self) -> dict[str, Any]:
        return self._send("renounceOwnership")


class Token(_ContractWrapper):
    def __init__(self, w3: Web3, address: str, account: LocalAccount) -> None:
        super().__init__(w3, address, account, PLACE_TOKEN_ABI)

    def name(self) -> str:
        return self._call("name")

    def symbol(self) -> str:
        return self._call("symbol")

    def decimals(self) -> int:
        return self._call("decimals")

    def total_supply(self) -> int:
        return self._call("totalSupply")

    def balance_of(self, address: str) -> int:
        return self._call("balanceOf", Web3.to_checksum_address(address))

    def allowance(self, owner: str, spender: str) -> int:
        return self._call(
            "allowance",
            Web3.to_checksum_address(owner),
            Web3.to_checksum_address(spender),
        )

    def approve(self, spender: str, value: int) -> dict[str, Any]:
        return self._send("approve", Web3.to_checksum_address(spender), value)

    def transfer(self, to: str, value: int) -> dict[str, Any]:
        return self._send("transfer", Web3.to_checksum_address(to), value)

    def transfer_from(self, from_: str, to: str, value: int) -> dict[str, Any]:
        return self._send(
            "transferFrom",
            Web3.to_checksum_address(from_),
            Web3.to_checksum_address(to),
            value,
        )


class Grid(_ContractWrapper):
    def __init__(self, w3: Web3, address: str, account: LocalAccount) -> None:
        super().__init__(w3, address, account, PLACE_GRID_ABI)

    def token(self) -> str:
        return self._call("TOKEN")

    def faucet(self) -> str:
        return self._call("FAUCET")

    def grid_size(self) -> int:
        return self._call("GRID_SIZE")

    def max_bulk(self) -> int:
        return self._call("MAX_BULK")

    def rent_price(self) -> int:
        return self._call("rentPrice")

    def rent_duration(self) -> int:
        return self._call("rentDuration")

    def owner(self) -> str:
        return self._call("owner")

    def cells(self, cell_id: int) -> dict[str, Any]:
        renter, color, expiry = self._call("cells", cell_id)
        return {"renter": renter, "color": color, "expiry": expiry}

    def rent_cell(self, x: int, y: int, color: str) -> dict[str, Any]:
        fn = self._contract.get_function_by_signature("rentCell(uint16,uint16,uint24)")(
            x, y, parse_color(color)
        )
        return self._send_fn(fn)

    def set_color(self, x: int, y: int, color: str) -> dict[str, Any]:
        return self._send("setColor", x, y, parse_color(color))

    def bulk_rent_cells(
        self, xs: list[int], ys: list[int], colors: list[str]
    ) -> dict[str, Any]:
        return self._send("bulkRentCells", xs, ys, [parse_color(c) for c in colors])

    def bulk_set_colors(
        self, xs: list[int], ys: list[int], colors: list[str]
    ) -> dict[str, Any]:
        return self._send("bulkSetColors", xs, ys, [parse_color(c) for c in colors])

    def set_rent_price(self, new_price: int) -> dict[str, Any]:
        return self._send("setRentPrice", new_price)

    def set_rent_duration(self, new_duration: int) -> dict[str, Any]:
        return self._send("setRentDuration", new_duration)

    def transfer_ownership(self, new_owner: str) -> dict[str, Any]:
        return self._send("transferOwnership", Web3.to_checksum_address(new_owner))

    def renounce_ownership(self) -> dict[str, Any]:
        return self._send("renounceOwnership")
