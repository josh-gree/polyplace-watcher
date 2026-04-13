import asyncio
from pathlib import Path

from web3 import AsyncWeb3, Web3
from web3.exceptions import PersistentConnectionError
from web3.types import LogReceipt
from websockets.exceptions import ConnectionClosed

from polyplace_contracts import PLACE_GRID_ABI
from polyplace_contracts.deploy import Deployment

from polyplace_watcher.events import CellColorUpdated, CellRented
from polyplace_watcher.grid import Grid
from polyplace_watcher.snapshot import Snapshot


def _event_topic(abi: list, name: str) -> bytes:
    event = next(e for e in abi if e.get("type") == "event" and e["name"] == name)
    sig = "{}({})".format(name, ",".join(i["type"] for i in event["inputs"]))
    return Web3.keccak(text=sig)


_CELL_RENTED_TOPIC = _event_topic(PLACE_GRID_ABI, "CellRented")
_CELL_COLOR_UPDATED_TOPIC = _event_topic(PLACE_GRID_ABI, "CellColorUpdated")


class Watcher:
    def __init__(self, http_url: str, ws_url: str, deployment: Deployment) -> None:
        self.grid = Grid()
        self._w3 = Web3(Web3.HTTPProvider(http_url))
        self._ws_url = ws_url
        self._contract = self._w3.eth.contract(address=deployment.grid, abi=PLACE_GRID_ABI)
        self._deployment = deployment
        self._last_block: int | None = None
        self._ws_w3: AsyncWeb3 | None = None

    def _decode_log(self, log: LogReceipt) -> CellRented | CellColorUpdated | None:
        topic = log["topics"][0]
        if topic == _CELL_RENTED_TOPIC:
            args = self._contract.events.CellRented().process_log(log)["args"]
            return CellRented(cell_id=args["cellId"], renter=args["renter"], expires_at=args["expiresAt"])
        if topic == _CELL_COLOR_UPDATED_TOPIC:
            args = self._contract.events.CellColorUpdated().process_log(log)["args"]
            return CellColorUpdated(cell_id=args["cellId"], renter=args["renter"], color=args["color"])
        return None

    def backfill(self, from_block: int) -> None:
        logs = self._w3.eth.get_logs({
            "address": self._deployment.grid,
            "fromBlock": from_block,
            "toBlock": "latest",
            "topics": [[_CELL_RENTED_TOPIC, _CELL_COLOR_UPDATED_TOPIC]],
        })
        for log in logs:
            self._last_block = log["blockNumber"]
            event = self._decode_log(log)
            if event is not None:
                self.grid.apply(event)

    def save_snapshot(self, path: Path) -> None:
        if self._last_block is None:
            raise ValueError("No blocks processed yet; cannot save snapshot.")
        snap = Snapshot(last_block=self._last_block, cells=dict(self.grid._cells))
        path.write_text(snap.model_dump_json())

    def load_snapshot(self, path: Path) -> None:
        snap = Snapshot.model_validate_json(path.read_text())
        self.grid._cells = snap.cells
        self._last_block = snap.last_block

    async def watch(self) -> None:
        while True:
            try:
                async with AsyncWeb3(AsyncWeb3.WebSocketProvider(self._ws_url)) as w3:
                    self._ws_w3 = w3
                    await w3.eth.subscribe("logs", {
                        "address": self._deployment.grid,
                        "topics": [[_CELL_RENTED_TOPIC, _CELL_COLOR_UPDATED_TOPIC]],
                    })
                    async for response in w3.socket.process_subscriptions():
                        self._last_block = response["result"]["blockNumber"]
                        event = self._decode_log(response["result"])
                        if event is not None:
                            self.grid.apply(event)
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, PersistentConnectionError):
                if self._last_block is not None:
                    self.backfill(self._last_block)
            finally:
                self._ws_w3 = None
