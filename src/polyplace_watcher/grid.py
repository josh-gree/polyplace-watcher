from datetime import datetime

from pydantic import BaseModel

from polyplace_watcher.events import CellColorUpdated, CellRented, RGB


class Cell(BaseModel):
    renter: str | None = None
    expires_at: datetime | None = None
    color: RGB | None = None


class Grid:
    def __init__(self) -> None:
        self._cells: dict[int, Cell] = {}

    def apply(self, event: CellRented | CellColorUpdated) -> None:
        existing = self._cells.get(event.cell_id, Cell())
        match event:
            case CellRented():
                self._cells[event.cell_id] = existing.model_copy(
                    update={"renter": event.renter, "expires_at": event.expires_at}
                )
            case CellColorUpdated():
                self._cells[event.cell_id] = existing.model_copy(
                    update={"color": event.color}
                )

    def get(self, cell_id: int) -> Cell | None:
        return self._cells.get(cell_id)
