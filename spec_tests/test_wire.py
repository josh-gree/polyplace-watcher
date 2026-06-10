"""SPEC §5 — the frozen /grid binary format.

The oracle for this format is the frontend's decoder:
`polyplace-frontend/src/live-updates.js :: parseSnapshot`. The reference
decoder below is a line-for-line transliteration of it — if `encode_grid`
output parses correctly through this, it parses in the real frontend.

Layout (per SPEC §5 and the frontend decoder):

    offset 0   magic            b"PLG\\x01"
    offset 4   width            u16 LE
    offset 6   height           u16 LE
    offset 8   n_renters        u32 LE
    offset 12  n_rented         u32 LE
    offset 16  meta_offset      u32 LE
    offset 20  address table    n_renters x 20 raw bytes
    then       color bitmap     ceil(width*height / 8) bytes,
                                bit (cell_id & 7) of byte (cell_id >> 3)
    then       packed RGB       3 bytes per set bit, ascending cell_id
    meta_off   rental records   n_rented x 11 bytes:
                                u16 LE + u8  (u24 cell_id, low/high)
                                u32 LE       (expires, unix s; 0 = none)
                                u32 LE       (renter index into table)

Pure tests: no chain, no service.
"""

import math
import struct
from dataclasses import dataclass, field

from polyplace_watcher import CellColorUpdated, CellRented, Grid, decode_grid, encode_grid

GRID_SIZE = 1000
TOTAL_CELLS = GRID_SIZE * GRID_SIZE
BITMAP_SIZE = math.ceil(TOTAL_CELLS / 8)  # 125_000
HEADER_SIZE = 20

RENTER_A = "0x" + "aa" * 20
RENTER_B = "0x" + "bb" * 20


# --- reference decoder: transliteration of live-updates.js parseSnapshot ----


@dataclass
class FrontendView:
    """What the frontend extracts from the snapshot — nothing more."""

    colors: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    rental_expires: dict[int, int] = field(default_factory=dict)
    rental_renter_idx: dict[int, int] = field(default_factory=dict)
    renter_table: list[str] = field(default_factory=list)


def parse_like_frontend(buf: bytes) -> FrontendView:
    assert buf[:4] == b"PLG\x01", "Bad magic bytes in snapshot"

    width, height, n_renters, n_rented, meta_offset = struct.unpack_from("<HHIII", buf, 4)

    bitmap_start = 20 + n_renters * 20
    total_cells = width * height
    bitmap_size = math.ceil(total_cells / 8)
    rgb_start = bitmap_start + bitmap_size

    view = FrontendView()
    # (The frontend skips the address table; we record it so tests can also
    # check the renter side, which SPEC §5 carries for non-frontend clients.)
    for i in range(n_renters):
        view.renter_table.append("0x" + buf[20 + i * 20 : 20 + (i + 1) * 20].hex())

    rgb_pos = rgb_start
    for cell_id in range(total_cells):
        if buf[bitmap_start + (cell_id >> 3)] & (1 << (cell_id & 7)):
            view.colors[cell_id] = (buf[rgb_pos], buf[rgb_pos + 1], buf[rgb_pos + 2])
            rgb_pos += 3

    pos = meta_offset
    for _ in range(n_rented):
        (low, high) = struct.unpack_from("<HB", buf, pos)
        cell_id = low | (high << 16)
        (expires,) = struct.unpack_from("<I", buf, pos + 3)
        (renter_idx,) = struct.unpack_from("<I", buf, pos + 7)
        view.rental_expires[cell_id] = expires
        view.rental_renter_idx[cell_id] = renter_idx
        pos += 11

    return view


def fold(events) -> Grid:
    grid = Grid()
    for event in events:
        grid.apply(event)
    return grid


# --- golden bytes ------------------------------------------------------------


def test_empty_grid_golden_bytes():
    """SPEC §5: the format always carries the full presence bitmap, even
    empty — the frontend sizes its scan from width*height, not content.

    Exact expected bytes: header with zero counts, meta_offset pointing
    just past the empty color section, all-zero bitmap, nothing else.
    """
    expected = (
        b"PLG\x01"
        + struct.pack("<HHIII", GRID_SIZE, GRID_SIZE, 0, 0, HEADER_SIZE + BITMAP_SIZE)
        + bytes(BITMAP_SIZE)
    )
    assert encode_grid(Grid()) == expected


def test_single_rented_colored_cell_golden_bytes():
    """SPEC §5: one fully-populated cell, every section exercised, byte-exact.

    Cell 5, renter RENTER_A, expires 1_750_000_000, color (1, 2, 3):
    bit 5 of bitmap byte 0; one 20-byte address; one 11-byte rental record.
    """
    grid = fold([
        CellRented(cell_id=5, renter=RENTER_A, expires_at=1_750_000_000),
        CellColorUpdated(cell_id=5, renter=RENTER_A, color=(1, 2, 3)),
    ])

    meta_offset = HEADER_SIZE + 20 + BITMAP_SIZE + 3
    bitmap = bytearray(BITMAP_SIZE)
    bitmap[0] = 1 << 5

    expected = (
        b"PLG\x01"
        + struct.pack("<HHIII", GRID_SIZE, GRID_SIZE, 1, 1, meta_offset)
        + bytes.fromhex("aa" * 20)                    # address table
        + bytes(bitmap)                               # presence bitmap
        + bytes([1, 2, 3])                            # packed RGB
        + struct.pack("<HB", 5, 0)                    # u24 cell_id
        + struct.pack("<I", 1_750_000_000)            # expires
        + struct.pack("<I", 0)                        # renter index
    )
    assert encode_grid(grid) == expected


# --- through the frontend's eyes ---------------------------------------------


def test_frontend_decoder_sees_the_grid_state():
    """SPEC §5: "`/grid` returns the custom binary format" — verified through
    a transliteration of the actual frontend parser (live-updates.js).

    Mixed population: colored+rented cells, a re-rented cell, a u24 cell id
    above 65535, and two renters sharing the address intern table.
    """
    events = [
        CellRented(cell_id=0, renter=RENTER_A, expires_at=1_000),
        CellColorUpdated(cell_id=0, renter=RENTER_A, color=(255, 0, 0)),
        CellRented(cell_id=70_000, renter=RENTER_B, expires_at=2_000),
        CellColorUpdated(cell_id=70_000, renter=RENTER_B, color=(0, 255, 0)),
        CellRented(cell_id=999_999, renter=RENTER_A, expires_at=3_000),
        CellColorUpdated(cell_id=999_999, renter=RENTER_A, color=(0, 0, 255)),
        # re-rent: cell 0 changes hands, color survives (§2)
        CellRented(cell_id=0, renter=RENTER_B, expires_at=4_000),
    ]
    view = parse_like_frontend(encode_grid(fold(events)))

    assert view.colors == {
        0: (255, 0, 0),
        70_000: (0, 255, 0),
        999_999: (0, 0, 255),
    }
    assert view.rental_expires == {0: 4_000, 70_000: 2_000, 999_999: 3_000}

    # Renter side (carried for non-frontend clients): indices resolve through
    # the table to the right lowercase addresses.
    resolved = {
        cell: view.renter_table[idx] for cell, idx in view.rental_renter_idx.items()
    }
    assert resolved == {0: RENTER_B, 70_000: RENTER_B, 999_999: RENTER_A}


def test_colorless_and_rentalless_cells_land_in_the_right_sections():
    """SPEC §2/§5: a rented-but-colorless cell (the transient mid-rent state)
    appears in the rental records but NOT in the bitmap; a colored cell is in
    the bitmap regardless of rental state. Expires 0 means "no expiry" to the
    frontend (`exp !== 0` in cell-store.js).
    """
    events = [
        CellRented(cell_id=10, renter=RENTER_A, expires_at=5_000),   # no color
        CellColorUpdated(cell_id=20, renter=RENTER_A, color=(7, 8, 9)),  # no rental
    ]
    view = parse_like_frontend(encode_grid(fold(events)))

    assert 10 not in view.colors
    assert view.rental_expires[10] == 5_000
    assert view.colors[20] == (7, 8, 9)
    assert 20 not in view.rental_expires


# --- determinism and roundtrip ----------------------------------------------


def test_encoding_is_deterministic_for_equal_logical_state():
    """SPEC §5: "Encoding is deterministic — a pure function of logical grid
    state. Canonical renter-table order: first appearance, scanning cells in
    ascending cell_id."

    Two grids reaching the same state by different event orders must encode
    to identical bytes.
    """
    grid_one = fold([
        CellRented(cell_id=3, renter=RENTER_A, expires_at=1_000),
        CellRented(cell_id=10, renter=RENTER_B, expires_at=2_000),
    ])
    grid_two = fold([
        CellRented(cell_id=10, renter=RENTER_B, expires_at=2_000),
        CellRented(cell_id=3, renter=RENTER_A, expires_at=1_000),
    ])
    data = encode_grid(grid_one)
    assert data == encode_grid(grid_two)

    # Canonical table order: RENTER_A first (cell 3 < cell 10).
    assert parse_like_frontend(data).renter_table == [RENTER_A, RENTER_B]


def test_decode_grid_roundtrips():
    """SPEC §5 pins `decode_grid` as the executable documentation of the
    format: decode(encode(grid)) reproduces every cell.
    """
    events = [
        CellRented(cell_id=0, renter=RENTER_A, expires_at=1_000),
        CellColorUpdated(cell_id=0, renter=RENTER_A, color=(255, 0, 0)),
        CellRented(cell_id=70_000, renter=RENTER_B, expires_at=2_000),
        CellColorUpdated(cell_id=20, renter=RENTER_A, color=(7, 8, 9)),
    ]
    grid = fold(events)
    decoded = decode_grid(encode_grid(grid))

    assert set(decoded.cells) == set(grid.cells)
    for cell_id, cell in grid.cells.items():
        twin = decoded.cells[cell_id]
        assert (twin.renter, twin.expires_at, twin.color) == (
            cell.renter,
            cell.expires_at,
            cell.color,
        ), f"cell {cell_id} did not roundtrip"
