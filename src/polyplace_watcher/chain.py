"""Chain access (SPEC §3): JSON-RPC over HTTP for backfill, an
``eth_subscribe("logs")`` WebSocket for live tailing, and the decoding of
the only two event types the watcher consumes.

Every other event the contract emits (OwnershipTransferred,
RentPriceUpdated, RentDurationUpdated) fails the topic match in
``decode_log`` and is ignored, not decoded.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
import websockets

from .core import CellColorUpdated, CellRented, Event

# keccak-256 of the PlaceGrid.sol event signatures; the deployed contract is
# immutable (SPEC §2), and the integration suite verifies these end to end.
TOPIC_CELL_RENTED = "0xd84d4c02e09d96adca6ea6758b66be9518218f5fd9e0a340f35961250a678fa9"  # CellRented(uint32,address,uint256)
TOPIC_CELL_COLOR_UPDATED = "0xfecf19208b33b21ad80a1e58f7091ef7eda20cd8ef2534eef68423c9b0182928"  # CellColorUpdated(uint32,address,uint24)


class RpcError(Exception):
    pass


@dataclass(frozen=True)
class ChainEvent:
    """A consumed event together with its position in the chain's log order."""

    event: Event
    block: int
    log_index: int


def decode_log(log: dict) -> ChainEvent | None:
    """Decode one raw eth log, or return None for event types the watcher
    must ignore (SPEC §3)."""
    topics = log["topics"]
    if not topics:
        return None
    event: Event
    if topics[0] == TOPIC_CELL_RENTED:
        event = CellRented(
            cell_id=int(topics[1], 16),
            renter="0x" + topics[2][-40:],
            expires_at=int(log["data"], 16),
        )
    elif topics[0] == TOPIC_CELL_COLOR_UPDATED:
        packed = int(log["data"], 16)
        event = CellColorUpdated(
            cell_id=int(topics[1], 16),
            renter="0x" + topics[2][-40:],
            color=((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF),
        )
    else:
        return None
    return ChainEvent(event=event, block=int(log["blockNumber"], 16), log_index=int(log["logIndex"], 16))


async def _call(http: httpx.AsyncClient, url: str, method: str, params: list) -> object:
    response = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RpcError(f"{method}: {payload['error']}")
    return payload["result"]


async def block_number(http: httpx.AsyncClient, url: str) -> int:
    return int(await _call(http, url, "eth_blockNumber", []), 16)


async def get_logs(http: httpx.AsyncClient, url: str, address: str, from_block: int, to_block: int) -> list[dict]:
    params = [{"address": address, "fromBlock": hex(from_block), "toBlock": hex(to_block)}]
    return await _call(http, url, "eth_getLogs", params)


@asynccontextmanager
async def subscribe_logs(ws_url: str, address: str) -> AsyncIterator[AsyncIterator[dict]]:
    """Subscribe to all logs of one contract address; yield the raw-log stream.

    max_queue=None: the subscription is opened before backfill (SPEC §4) and
    not consumed until backfill finishes, so the library must buffer freely
    rather than backpressure the connection into a keepalive timeout.
    """
    async with websockets.connect(ws_url, max_queue=None) as ws:
        await ws.send(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["logs", {"address": address}]}
        ))
        reply = json.loads(await ws.recv())
        if "error" in reply:
            raise RpcError(f"eth_subscribe: {reply['error']}")
        yield _notifications(ws)


async def _notifications(ws) -> AsyncIterator[dict]:
    async for raw in ws:
        message = json.loads(raw)
        if message.get("method") == "eth_subscription":
            yield message["params"]["result"]
