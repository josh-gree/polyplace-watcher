from pydantic import BaseModel

from polyplace_watcher.grid import Cell


class Snapshot(BaseModel):
    last_block: int
    last_log_index: int | None = None
    cells: dict[int, Cell]
