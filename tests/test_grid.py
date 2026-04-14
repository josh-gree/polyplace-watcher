import pytest

from polyplace_watcher.events import CellColorUpdated, CellRented, RGB
from polyplace_watcher.grid import Cell, Grid


RENTER = "0xabc"
EXPIRES_AT = 1712345678

# Valid 20-byte Ethereum addresses for serialisation tests
ADDR_A = "0x" + "ab" * 20
ADDR_B = "0x" + "cd" * 20


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


# --- serialisation round-trip ---


def test_roundtrip_empty_grid() -> None:
    grid = Grid()
    assert Grid.from_bytes(grid.to_bytes())._cells == {}


def test_roundtrip_color_only() -> None:
    grid = Grid()
    grid.apply(CellColorUpdated(cell_id=42, renter=ADDR_A, color=0xFF8800))
    rt = Grid.from_bytes(grid.to_bytes())
    assert rt.get(42) == Cell(color=RGB(r=255, g=136, b=0))


def test_roundtrip_rental_only() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=7, renter=ADDR_A, expires_at=EXPIRES_AT))
    rt = Grid.from_bytes(grid.to_bytes())
    cell = rt.get(7)
    assert cell is not None
    assert cell.renter == ADDR_A
    assert cell.expires_at is not None
    assert int(cell.expires_at.timestamp()) == EXPIRES_AT
    assert cell.color is None


def test_roundtrip_full_cell() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=5, renter=ADDR_A, expires_at=EXPIRES_AT))
    grid.apply(CellColorUpdated(cell_id=5, renter=ADDR_A, color=0x112233))
    rt = Grid.from_bytes(grid.to_bytes())
    cell = rt.get(5)
    assert cell is not None
    assert cell.renter == ADDR_A
    assert int(cell.expires_at.timestamp()) == EXPIRES_AT  # type: ignore[union-attr]
    assert cell.color == RGB(r=0x11, g=0x22, b=0x33)


def test_roundtrip_shared_renter() -> None:
    # Two cells with the same renter — address should appear once in the table
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT))
    grid.apply(CellRented(cell_id=1, renter=ADDR_A, expires_at=EXPIRES_AT))
    data = grid.to_bytes()
    import struct
    (n_renters,) = struct.unpack_from("<I", data, 8)
    assert n_renters == 1
    rt = Grid.from_bytes(data)
    assert rt.get(0).renter == ADDR_A  # type: ignore[union-attr]
    assert rt.get(1).renter == ADDR_A  # type: ignore[union-attr]


def test_roundtrip_multiple_renters() -> None:
    grid = Grid()
    grid.apply(CellRented(cell_id=0, renter=ADDR_A, expires_at=EXPIRES_AT))
    grid.apply(CellRented(cell_id=1, renter=ADDR_B, expires_at=EXPIRES_AT))
    rt = Grid.from_bytes(grid.to_bytes())
    assert rt.get(0).renter == ADDR_A  # type: ignore[union-attr]
    assert rt.get(1).renter == ADDR_B  # type: ignore[union-attr]


def test_roundtrip_high_cell_id() -> None:
    # cell_id near the top of the grid exercises the uint24 encoding
    cell_id = 999 * 1000 + 999  # (999, 999) — last cell
    grid = Grid()
    grid.apply(CellRented(cell_id=cell_id, renter=ADDR_A, expires_at=EXPIRES_AT))
    grid.apply(CellColorUpdated(cell_id=cell_id, renter=ADDR_A, color=0xDEADBE))
    rt = Grid.from_bytes(grid.to_bytes())
    cell = rt.get(cell_id)
    assert cell is not None
    assert cell.renter == ADDR_A
    assert cell.color == RGB(r=0xDE, g=0xAD, b=0xBE)


def test_roundtrip_bad_magic_raises() -> None:
    with pytest.raises(ValueError, match="bad magic"):
        Grid.from_bytes(b"NOPE" + b"\x00" * 100)


# --- idempotency ---


def test_cell_rented_applied_twice_is_idempotent() -> None:
    grid = Grid()
    event = CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT)
    grid.apply(event)
    cell_after_first = grid.get(0)
    grid.apply(event)
    assert grid.get(0) == cell_after_first


def test_cell_color_updated_applied_twice_is_idempotent() -> None:
    grid = Grid()
    event = CellColorUpdated(cell_id=0, renter=RENTER, color=0xFF8800)
    grid.apply(event)
    cell_after_first = grid.get(0)
    grid.apply(event)
    assert grid.get(0) == cell_after_first


def test_rented_then_color_updated_replayed_is_idempotent() -> None:
    grid = Grid()
    rent = CellRented(cell_id=0, renter=RENTER, expires_at=EXPIRES_AT)
    color = CellColorUpdated(cell_id=0, renter=RENTER, color=0xFF8800)
    grid.apply(rent)
    grid.apply(color)
    cell_after_first_pass = grid.get(0)
    grid.apply(rent)
    grid.apply(color)
    assert grid.get(0) == cell_after_first_pass
