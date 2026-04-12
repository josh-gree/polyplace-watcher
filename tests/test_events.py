from datetime import datetime, timezone

from polyplace_watcher.events import GRID_SIZE, CellColorUpdated, CellRented, RGB


def test_expires_at_parsed_as_datetime() -> None:
    event = CellRented(cell_id=1, renter="0xabc", expires_at=1712345678)
    assert event.expires_at == datetime(2024, 4, 5, 19, 34, 38, tzinfo=timezone.utc)


def test_expires_at_is_utc() -> None:
    event = CellRented(cell_id=1, renter="0xabc", expires_at=0)
    assert event.expires_at.tzinfo == timezone.utc


def test_color_unpacked() -> None:
    event = CellColorUpdated(cell_id=1, renter="0xabc", color=0xFF8800)
    assert event.color == RGB(r=0xFF, g=0x88, b=0x00)


def test_color_black() -> None:
    event = CellColorUpdated(cell_id=1, renter="0xabc", color=0x000000)
    assert event.color == RGB(r=0, g=0, b=0)


def test_color_white() -> None:
    event = CellColorUpdated(cell_id=1, renter="0xabc", color=0xFFFFFF)
    assert event.color == RGB(r=255, g=255, b=255)


def test_color_channels_dont_bleed() -> None:
    event = CellColorUpdated(cell_id=1, renter="0xabc", color=0x010203)
    assert event.color == RGB(r=1, g=2, b=3)


def test_cell_rented_xy_origin() -> None:
    event = CellRented(cell_id=0, renter="0xabc", expires_at=0)
    assert event.x == 0
    assert event.y == 0


def test_cell_rented_xy() -> None:
    event = CellRented(cell_id=2 * GRID_SIZE + 500, renter="0xabc", expires_at=0)
    assert event.x == 500
    assert event.y == 2


def test_cell_color_updated_xy_origin() -> None:
    event = CellColorUpdated(cell_id=0, renter="0xabc", color=0)
    assert event.x == 0
    assert event.y == 0


def test_cell_color_updated_xy() -> None:
    event = CellColorUpdated(cell_id=2 * GRID_SIZE + 500, renter="0xabc", color=0)
    assert event.x == 500
    assert event.y == 2
