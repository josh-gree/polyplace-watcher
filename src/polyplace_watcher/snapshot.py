from pydantic import BaseModel

from polyplace_watcher.grid import Cell


class Snapshot(BaseModel):
    last_block: int
    cells: dict[int, Cell]
