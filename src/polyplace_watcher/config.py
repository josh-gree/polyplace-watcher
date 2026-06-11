"""Startup configuration (SPEC §7). Read from the environment at startup,
never at import — the deploy flows import ``polyplace_watcher.app`` before
any per-machine env is meaningful.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    web3_http_url: str
    web3_ws_url: str
    grid_address: str  # lowercase
    start_block: int
    cors_origins: tuple[str, ...]
    backfill_chunk_size: int
    snapshot_dir: Path
    git_sha: str | None

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            web3_http_url=os.environ["WEB3_HTTP_URL"],
            web3_ws_url=os.environ["WEB3_WS_URL"],
            grid_address=os.environ["GRID_ADDRESS"].lower(),
            start_block=int(os.environ["START_BLOCK"]),
            cors_origins=tuple(
                origin.strip()
                for origin in os.environ.get("CORS_ORIGINS", "").split(",")
                if origin.strip()
            ),
            # RPC providers differ in eth_getLogs range limits (SPEC §7, D6).
            backfill_chunk_size=int(os.environ.get("BACKFILL_CHUNK_SIZE", "1000")),
            snapshot_dir=Path(os.environ.get("SNAPSHOT_DIR", ".")),
            git_sha=os.environ.get("POLYPLACE_GIT_SHA"),
        )

    @property
    def snapshot_path(self) -> Path:
        """Derived from the contract address, so a snapshot written for one
        GRID_ADDRESS can never be loaded for another (SPEC §7, D6)."""
        return self.snapshot_dir / f"snapshot-{self.grid_address}.json"


def scrub_url(url: str) -> str:
    """RPC URLs carry secrets in userinfo or the path (provider API keys);
    keep only scheme and host for logging (SPEC §8)."""
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    suffix = "/…" if parts.path.strip("/") or parts.query else ""
    return f"{parts.scheme}://{host}{suffix}"
