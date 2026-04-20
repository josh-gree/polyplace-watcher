"""Top-level Cli class exposed via python-fire as `polyplace-dev`."""

from __future__ import annotations

import fire
from eth_account.signers.local import LocalAccount
from web3 import Web3

from polyplace_contracts import Deployment

from .contracts import Faucet, Grid, Token
from .helpers import DEFAULT_RPC_URL, get_account, get_w3, load_addresses


class Cli:
    """polyplace-dev: local development CLI."""

    def __init__(self, rpc_url: str = DEFAULT_RPC_URL, account: str = "deployer") -> None:
        self._rpc_url = rpc_url
        self._account_key = account
        self.__w3: Web3 | None = None
        self.__account: LocalAccount | None = None
        self.__addresses: Deployment | None = None

    def _ctx(self) -> tuple[Web3, LocalAccount, Deployment]:
        if self.__w3 is None:
            self.__w3 = get_w3(self._rpc_url)
            self.__account = get_account(self.__w3, self._account_key)
            self.__addresses = load_addresses()
        return self.__w3, self.__account, self.__addresses  # type: ignore[return-value]

    @property
    def faucet(self) -> Faucet:
        w3, account, addrs = self._ctx()
        return Faucet(w3, addrs.faucet, account)

    @property
    def token(self) -> Token:
        w3, account, addrs = self._ctx()
        return Token(w3, addrs.token, account)

    @property
    def grid(self) -> Grid:
        w3, account, addrs = self._ctx()
        return Grid(w3, addrs.grid, account)


def main() -> None:
    fire.Fire(Cli)


if __name__ == "__main__":
    main()
