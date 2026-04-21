from __future__ import annotations

import os
from dataclasses import dataclass


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@dataclass(frozen=True)
class ContractsConfig:
    token: str
    faucet: str
    grid: str

    @classmethod
    def from_env(cls) -> "ContractsConfig":
        return cls(
            token=_require("TOKEN_ADDRESS"),
            faucet=_require("FAUCET_ADDRESS"),
            grid=_require("GRID_ADDRESS"),
        )


@dataclass(frozen=True)
class WatcherConfig:
    http_url: str
    ws_url: str
    start_block: int
    contracts: ContractsConfig

    @classmethod
    def from_env(cls) -> "WatcherConfig":
        return cls(
            http_url=_require("WEB3_HTTP_URL"),
            ws_url=_require("WEB3_WS_URL"),
            start_block=int(_require("START_BLOCK")),
            contracts=ContractsConfig.from_env(),
        )
