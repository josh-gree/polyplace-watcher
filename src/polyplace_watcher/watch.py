"""The time-and-failure machinery (SPEC §4): subscribe before backfill,
backfill in chunks, tail the live subscription, checkpoint periodically,
and retry forever.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import httpx

from . import chain
from .chain import ChainEvent
from .config import Config, scrub_url
from .core import Grid
from .snapshot import save_snapshot

log = logging.getLogger(__name__)

# Incidental constants (SPEC §4): the intrinsic requirements are "restart
# cost stays bounded", "never die", and "don't hammer a struggling RPC".
CHECKPOINT_SECONDS = 60
INITIAL_BACKOFF = 0.5
MAX_BACKOFF = 60.0


class Store:
    """Everything the process knows about the chain, plus the /ws fanout.

    Every applied event — backfilled or live — flows through ``apply``:
    deltas have one path (SPEC §4). The (block, log_index) watermark makes
    the overlap that the resume rule and the backfill/live seam produce
    (SPEC §2) invisible to clients: re-delivered events are skipped, so
    state stays monotone and no delta is broadcast twice.
    """

    def __init__(self) -> None:
        self.grid = Grid()
        self.last_block: int | None = None  # position of the last applied event
        self.last_log_index: int | None = None
        self.scanned_to: int | None = None  # highest block known fully scanned
        self.changed_since_save = False
        self._subscribers: set[asyncio.Queue[dict]] = set()

    def apply(self, applied: ChainEvent) -> None:
        if self.last_block is not None and (applied.block, applied.log_index) <= (
            self.last_block,
            self.last_log_index,
        ):
            return
        cell = self.grid.apply(applied.event)
        self.last_block, self.last_log_index = applied.block, applied.log_index
        self.changed_since_save = True
        if cell.color is None:
            # §5: the frontend assumes every delta carries r/g/b. This gate
            # withholds nothing — the color event paired with every rent
            # announces the cell one log index later (decision D2).
            return
        delta = {
            "i": applied.event.cell_id,
            "r": cell.color[0],
            "g": cell.color[1],
            "b": cell.color[2],
            "renter": cell.renter,
            "expires_at": cell.expires_at,
        }
        for queue in self._subscribers:
            queue.put_nowait(delta)

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(queue)


async def watch(config: Config, store: Store) -> None:
    """Follow the chain forever. Any failure is caught, logged, and retried
    with bounded backoff; only cancellation stops this loop (SPEC §4)."""
    backoff = INITIAL_BACKOFF
    while True:
        try:
            # Subscribe before backfilling, so no event can fall between
            # backfill and live (SPEC §4).
            async with chain.subscribe_logs(config.web3_ws_url, config.grid_address) as live:
                log.info("chain_subscribed", extra={"ws_url": scrub_url(config.web3_ws_url)})
                backoff = INITIAL_BACKOFF
                await _backfill(config, store)
                await _tail(live, store)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning(
                "chain_connection_lost",
                extra={"error": repr(error), "retry_in_seconds": backoff},
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)


async def _backfill(config: Config, store: Store) -> None:
    """Catch up over HTTP, from the resume point to the current head, in
    chunks. Events past the head are already buffering on the open live
    subscription; the Store watermark absorbs any overlap at the seam."""
    async with httpx.AsyncClient(timeout=30) as http:
        head = await chain.block_number(http, config.web3_http_url)
        start = max(
            config.start_block,
            store.last_block or 0,  # re-scan the block of the last applied event (SPEC §2, §4)
            store.scanned_to + 1 if store.scanned_to is not None else 0,
        )
        log.info("backfill_started", extra={"from_block": start, "to_block": head})
        for chunk_start in range(start, head + 1, config.backfill_chunk_size):
            chunk_end = min(chunk_start + config.backfill_chunk_size - 1, head)
            logs = await chain.get_logs(
                http, config.web3_http_url, config.grid_address, chunk_start, chunk_end
            )
            for raw in logs:
                applied = chain.decode_log(raw)
                if applied is not None:
                    store.apply(applied)
            store.scanned_to = chunk_end
            log.info("backfill_chunk_scanned", extra={"to_block": chunk_end, "log_count": len(logs)})


async def _tail(live: AsyncIterator[dict], store: Store) -> None:
    log.info("live_tail_started")
    async for raw in live:
        applied = chain.decode_log(raw)
        if applied is not None:
            store.apply(applied)
            # block - 1, not block: this block may hold further logs not yet
            # delivered, so it is not fully scanned. A reconnect must re-scan
            # the block of the last applied event (SPEC §2); the Store
            # watermark absorbs the overlap.
            store.scanned_to = max(store.scanned_to or 0, applied.block - 1)
    raise chain.RpcError("chain WebSocket closed")


async def checkpoint(config: Config, store: Store) -> None:
    """Periodically write the snapshot if the grid changed (SPEC §4). Like
    the watch loop, only cancellation stops it."""
    while True:
        await asyncio.sleep(CHECKPOINT_SECONDS)
        try:
            save_if_changed(config, store)
        except Exception:
            log.warning("snapshot_save_failed", exc_info=True)


def save_if_changed(config: Config, store: Store) -> None:
    if not store.changed_since_save:
        return
    save_snapshot(config.snapshot_path, store.grid, store.last_block, store.last_log_index)
    store.changed_since_save = False
    log.info(
        "snapshot_saved",
        extra={"path": str(config.snapshot_path), "last_block": store.last_block},
    )
