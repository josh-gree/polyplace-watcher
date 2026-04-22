import asyncio
import logging
from typing import AsyncIterator

from web3 import AsyncWeb3, Web3
from web3.exceptions import PersistentConnectionError
from web3.types import LogReceipt
from websockets.exceptions import ConnectionClosed

from polyplace_contracts import PLACE_GRID_ABI
from polyplace_watcher.config import ContractsConfig

from polyplace_watcher.events import CellColorUpdated, CellRented
from polyplace_watcher.grid_store import GridStore
from polyplace_watcher.observability import scrub_url

logger = logging.getLogger(__name__)


def _event_topic(abi: list, name: str) -> bytes:
    event = next(e for e in abi if e.get("type") == "event" and e["name"] == name)
    sig = "{}({})".format(name, ",".join(i["type"] for i in event["inputs"]))
    return Web3.keccak(text=sig)


_CELL_RENTED_TOPIC = _event_topic(PLACE_GRID_ABI, "CellRented")
_CELL_COLOR_UPDATED_TOPIC = _event_topic(PLACE_GRID_ABI, "CellColorUpdated")


class Watcher:
    def __init__(
        self,
        http_url: str,
        ws_url: str,
        contracts: ContractsConfig,
        start_block: int = 0,
        store: GridStore | None = None,
        backfill_chunk_size: int = 10_000,
    ) -> None:
        if backfill_chunk_size <= 0:
            raise ValueError(f"backfill_chunk_size must be positive, got {backfill_chunk_size}")
        self.store = store if store is not None else GridStore()
        self._w3 = Web3(Web3.HTTPProvider(http_url))
        self._ws_url = ws_url
        self._contract = self._w3.eth.contract(address=contracts.grid, abi=PLACE_GRID_ABI)
        self._contracts = contracts
        self._start_block: int = start_block
        self._backfill_chunk_size = backfill_chunk_size
        self._ws_w3: AsyncWeb3 | None = None
        logger.info(
            "watcher_initialized",
            extra={
                "component": "watcher",
                "config": {
                    "WEB3_HTTP_URL": scrub_url(http_url),
                    "WEB3_WS_URL": scrub_url(ws_url),
                    "GRID_ADDRESS": contracts.grid,
                    "TOKEN_ADDRESS": contracts.token,
                    "FAUCET_ADDRESS": contracts.faucet,
                    "START_BLOCK": start_block,
                    "BACKFILL_CHUNK_SIZE": backfill_chunk_size,
                },
            },
        )

    def _decode_log(self, log: LogReceipt) -> CellRented | CellColorUpdated | None:
        topic = log["topics"][0]
        if topic == _CELL_RENTED_TOPIC:
            args = self._contract.events.CellRented().process_log(log)["args"]
            return CellRented(cell_id=args["cellId"], renter=args["renter"], expires_at=args["expiresAt"])
        if topic == _CELL_COLOR_UPDATED_TOPIC:
            args = self._contract.events.CellColorUpdated().process_log(log)["args"]
            return CellColorUpdated(cell_id=args["cellId"], renter=args["renter"], color=args["color"])
        return None

    def fetch_logs(
        self, from_block: int, to_block: int
    ) -> list[tuple[CellRented | CellColorUpdated, int, int]]:
        logger.debug(
            "watcher_fetch_logs_started",
            extra={
                "component": "watcher",
                "from_block": from_block,
                "to_block": to_block,
            },
        )
        logs = self._w3.eth.get_logs({
            "address": self._contracts.grid,
            "fromBlock": from_block,
            "toBlock": to_block,
            "topics": [[_CELL_RENTED_TOPIC, _CELL_COLOR_UPDATED_TOPIC]],
        })
        result = []
        for log in logs:
            event = self._decode_log(log)
            if event is not None:
                result.append((event, log["blockNumber"], log["logIndex"]))
        logger.info(
            "watcher_fetch_logs_finished",
            extra={
                "component": "watcher",
                "from_block": from_block,
                "to_block": to_block,
                "log_count": len(result),
            },
        )
        return result

    def _current_head(self) -> int:
        return self._w3.eth.block_number

    async def _backfill(
        self, from_block: int
    ) -> AsyncIterator[tuple[CellRented | CellColorUpdated, int, int]]:
        head = await asyncio.to_thread(self._current_head)
        if from_block > head:
            return
        cursor = from_block
        while cursor <= head:
            chunk_end = min(cursor + self._backfill_chunk_size - 1, head)
            chunk = await asyncio.to_thread(self.fetch_logs, cursor, chunk_end)
            logger.info(
                "watcher_backfill_chunk",
                extra={
                    "component": "watcher",
                    "from_block": cursor,
                    "to_block": chunk_end,
                    "head": head,
                    "log_count": len(chunk),
                },
            )
            for item in chunk:
                yield item
            cursor = chunk_end + 1

    async def watch(self) -> None:
        connection_attempt = 0
        while True:
            try:
                connection_attempt += 1
                logger.info(
                    "watcher_connecting",
                    extra={
                        "component": "watcher",
                        "connection_attempt": connection_attempt,
                    },
                )
                async with AsyncWeb3(AsyncWeb3.WebSocketProvider(self._ws_url)) as w3:
                    self._ws_w3 = w3
                    logger.info(
                        "watcher_connected",
                        extra={
                            "component": "watcher",
                            "connection_attempt": connection_attempt,
                        },
                    )
                    await w3.eth.subscribe("logs", {
                        "address": self._contracts.grid,
                        "topics": [[_CELL_RENTED_TOPIC, _CELL_COLOR_UPDATED_TOPIC]],
                    })
                    logger.info(
                        "watcher_subscribed",
                        extra={
                            "component": "watcher",
                            "connection_attempt": connection_attempt,
                        },
                    )
                    from_block = self.store.last_block if self.store.last_block is not None else self._start_block
                    logger.info(
                        "watcher_backfill_started",
                        extra={
                            "component": "watcher",
                            "from_block": from_block,
                        },
                    )
                    backfill_count = 0
                    async for event, block, log_index in self._backfill(from_block):
                        self.store.apply(event, block, log_index)
                        backfill_count += 1
                    logger.info(
                        "watcher_backfill_finished",
                        extra={
                            "component": "watcher",
                            "from_block": from_block,
                            "log_count": backfill_count,
                            "last_block": self.store.last_block,
                            "last_log_index": self.store.last_log_index,
                        },
                    )
                    async for response in w3.socket.process_subscriptions():
                        event = self._decode_log(response["result"])
                        if event is not None:
                            self.store.apply(
                                event,
                                response["result"]["blockNumber"],
                                response["result"]["logIndex"],
                            )
                            logger.debug(
                                "watcher_live_event_applied",
                                extra={
                                    "component": "watcher",
                                    "event_type": type(event).__name__,
                                    "cell_id": event.cell_id,
                                    "block": response["result"]["blockNumber"],
                                    "log_index": response["result"]["logIndex"],
                                },
                            )
            except asyncio.CancelledError:
                logger.info("watcher_cancelled", extra={"component": "watcher"})
                raise
            except (ConnectionClosed, PersistentConnectionError) as exc:
                logger.warning(
                    "watcher_connection_lost",
                    extra={
                        "component": "watcher",
                        "connection_attempt": connection_attempt,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
            finally:
                self._ws_w3 = None
