"""The frozen /grid binary format (SPEC §5).

Frozen contract with the frontend (its decoder is
``polyplace-frontend/src/live-updates.js :: parseSnapshot``):

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

Encoding is deterministic — a pure function of logical grid state.
"""

from __future__ import annotations

import math
import struct

from .core import Cell, Grid

GRID_SIZE = 1000
BITMAP_SIZE = math.ceil(GRID_SIZE * GRID_SIZE / 8)

MAGIC = b"PLG\x01"
HEADER = struct.Struct("<HHIII")  # width, height, n_renters, n_rented, meta_offset
RENTAL = struct.Struct("<HBII")  # cell_id low u16 / high u8, expires u32, renter index u32


def encode_grid(grid: Grid) -> bytes:
    cells = sorted(grid.cells.items())
    rented = [(cell_id, cell) for cell_id, cell in cells if cell.renter is not None]
    colored = [(cell_id, cell) for cell_id, cell in cells if cell.color is not None]

    # Canonical renter-table order: first appearance, scanning cells in
    # ascending cell_id (SPEC §5).
    renter_index: dict[str, int] = {}
    for _, cell in rented:
        renter_index.setdefault(cell.renter, len(renter_index))

    bitmap = bytearray(BITMAP_SIZE)
    rgb = bytearray()
    for cell_id, cell in colored:
        bitmap[cell_id >> 3] |= 1 << (cell_id & 7)
        rgb.extend(cell.color)

    meta_offset = 4 + HEADER.size + 20 * len(renter_index) + BITMAP_SIZE + len(rgb)
    parts = [
        MAGIC,
        HEADER.pack(GRID_SIZE, GRID_SIZE, len(renter_index), len(rented), meta_offset),
        b"".join(bytes.fromhex(address[2:]) for address in renter_index),
        bytes(bitmap),
        bytes(rgb),
    ]
    parts += [
        RENTAL.pack(cell_id & 0xFFFF, cell_id >> 16, cell.expires_at or 0, renter_index[cell.renter])
        for cell_id, cell in rented
    ]
    return b"".join(parts)


def decode_grid(data: bytes) -> Grid:
    """Inverse of encode_grid — the executable documentation of the format."""
    if data[:4] != MAGIC:
        raise ValueError("not a PLG\\x01 grid")
    width, height, n_renters, n_rented, meta_offset = HEADER.unpack_from(data, 4)

    table_start = 4 + HEADER.size
    bitmap_start = table_start + 20 * n_renters
    rgb_start = bitmap_start + math.ceil(width * height / 8)
    renters = [
        "0x" + data[table_start + 20 * i : table_start + 20 * (i + 1)].hex()
        for i in range(n_renters)
    ]

    grid = Grid()

    def cell(cell_id: int) -> Cell:
        return grid.cells.setdefault(cell_id, Cell())

    rgb_pos = rgb_start
    for byte_index, byte in enumerate(data[bitmap_start:rgb_start]):
        for bit in range(8) if byte else ():
            if byte & (1 << bit):
                cell(byte_index * 8 + bit).color = tuple(data[rgb_pos : rgb_pos + 3])
                rgb_pos += 3

    for record in range(n_rented):
        low, high, expires_at, renter = RENTAL.unpack_from(data, meta_offset + RENTAL.size * record)
        rented = cell(low | (high << 16))
        rented.renter = renters[renter]
        rented.expires_at = expires_at or None  # 0 on the wire means "no expiry"
    return grid
