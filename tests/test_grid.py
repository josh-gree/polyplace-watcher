import pytest

from polyplace_watcher.events import CellColorUpdated, CellRented, RGB
from polyplace_watcher.grid import Cell, Grid


RENTER = "0xabc"
EXPIRES_AT = 1712345678


def test_get_unknown_cell_returns_none() -> None:
    grid = Grid()
    assert grid.get(0) is None


def test_cell_rented_sets_renter_and_expiry() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT))
    cell = grid.get(0)
    assert cell is not None
    assert cell.renter == RENTER
    assert cell.expires_at is not None
    assert cell.expires_at.timestamp() == EXPIRES_AT


def test_cell_rented_does_not_set_color() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT))
    assert grid.get(0).color is None  # type: ignore[union-attr]


def test_cell_color_updated_sets_color() -> None:
    grid = Grid()
    grid.apply(CellColorUpdated(cell_id=0, renter=RENTER, color=0xFF8800))
    cell = grid.get(0)
    assert cell is not None
    assert cell.color == RGB(r=255, g=136, b=0)


def test_cell_color_updated_does_not_set_renter_or_expiry() -> None:
    grid = Grid()
    grid.apply(CellColorUpdated(cell_id=0, renter=RENTER, color=0xFF8800))
    cell = grid.get(0)
    assert cell is not None
    assert cell.renter is None
    assert cell.expires_at is None


def test_rented_then_color_updated() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT))
    grid.apply(CellColorUpdated(cell_id=0, renter=RENTER, color=0xFF8800))
    cell = grid.get(0)
    assert cell is not None
    assert cell.renter == RENTER
    assert cell.color == RGB(r=255, g=136, b=0)


def test_color_update_does_not_overwrite_expiry() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT))
    grid.apply(CellColorUpdated(cell_id=0, renter=RENTER, color=0xFF8800))
    assert grid.get(0).expires_at.timestamp() == EXPIRES_AT  # type: ignore[union-attr]


def test_rent_update_does_not_overwrite_color() -> None:
    grid = Grid()
    grid.apply(CellColorUpdated(cell_id=0, renter=RENTER, color=0xFF8800))
    grid.apply(CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT))
    assert grid.get(0).color == RGB(r=255, g=136, b=0)  # type: ignore[union-attr]


def test_different_cells_are_independent() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT))
    grid.apply(CellColorUpdated(cell_id=1, renter=RENTER, color=0xFF8800))
    assert grid.get(0).color is None  # type: ignore[union-attr]
    assert grid.get(1).renter is None  # type: ignore[union-attr]
