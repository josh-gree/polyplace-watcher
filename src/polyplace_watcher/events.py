from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, computed_field

GRID_SIZE = 1000


class RGB(BaseModel):
    r: int
    g: int
    b: int


def _unpack_rgb(v: object) -> object:
    if isinstance(v, int):
        return RGB(r=(v >> 16) & 0xFF, g=(v >> 8) & 0xFF, b=v & 0xFF)
    return v


def _unix_to_datetime(v: object) -> object:
    if isinstance(v, int):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    return v


PackedRGB = Annotated[RGB, BeforeValidator(_unpack_rgb)]
UnixTimestamp = Annotated[datetime, BeforeValidator(_unix_to_datetime)]


class CellRented(BaseModel):
    cell_id: int
    renter: str
    expires_at: UnixTimestamp

    @computed_field  # type: ignore[prop-decorator]
    @property
    def x(self) -> int:
        return self.cell_id % GRID_SIZE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def y(self) -> int:
        return self.cell_id // GRID_SIZE


class CellColorUpdated(BaseModel):
    cell_id: int
    renter: str
    color: PackedRGB

    @computed_field  # type: ignore[prop-decorator]
    @property
    def x(self) -> int:
        return self.cell_id % GRID_SIZE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def y(self) -> int:
        return self.cell_id // GRID_SIZE
