from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractsConfig:
    token: str
    faucet: str
    grid: str

    @classmethod
    def from_env(cls) -> "ContractsConfig":
        return cls(
            token=os.environ["TOKEN_ADDRESS"],
            faucet=os.environ["FAUCET_ADDRESS"],
            grid=os.environ["GRID_ADDRESS"],
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
            http_url=os.environ["WEB3_HTTP_URL"],
            ws_url=os.environ["WEB3_WS_URL"],
            start_block=int(os.environ["START_BLOCK"]),
            contracts=ContractsConfig.from_env(),
        )
