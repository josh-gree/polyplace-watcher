from __future__ import annotations

import os
from dataclasses import dataclass

from web3 import Web3


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _require_address(name: str) -> str:
    return Web3.to_checksum_address(_require(name))


@dataclass(frozen=True)
class ContractsConfig:
    grid: str

    @classmethod
    def from_env(cls) -> "ContractsConfig":
        return cls(
            grid=_require_address("GRID_ADDRESS"),
        )


@dataclass(frozen=True)
class WatcherConfig:
    http_url: str
    ws_url: str
    start_block: int
    contracts: ContractsConfig
    backfill_chunk_size: int = 10_000

    @classmethod
    def from_env(cls) -> "WatcherConfig":
        return cls(
            http_url=_require("WEB3_HTTP_URL"),
            ws_url=_require("WEB3_WS_URL"),
            start_block=int(_require("START_BLOCK")),
            contracts=ContractsConfig.from_env(),
            backfill_chunk_size=int(os.environ.get("BACKFILL_CHUNK_SIZE", "10000")),
        )
