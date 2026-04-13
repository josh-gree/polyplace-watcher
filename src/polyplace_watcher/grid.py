import math
import struct
from datetime import datetime, timezone

from pydantic import BaseModel, field_validator

from polyplace_watcher.events import GRID_SIZE, CellColorUpdated, CellRented, RGB

_MAGIC = b"PLG\x01"


class Cell(BaseModel):
    renter: str | None = None
    expires_at: datetime | None = None
    color: RGB | None = None

    @field_validator("renter")
    @classmethod
    def _lowercase_renter(cls, v: str | None) -> str | None:
        return v.lower() if v is not None else None


class Grid:
    def __init__(self) -> None:
        self._cells: dict[int, Cell] = {}

    def apply(self, event: CellRented | CellColorUpdated) -> None:
        existing = self._cells.get(event.cell_id, Cell())
        match event:
            case CellRented():
                self._cells[event.cell_id] = Cell(
                    color=existing.color,
                    renter=event.renter,
                    expires_at=event.expires_at,
                )
            case CellColorUpdated():
                self._cells[event.cell_id] = Cell(
                    color=event.color,
                    renter=existing.renter,
                    expires_at=existing.expires_at,
                )

    def get(self, cell_id: int) -> Cell | None:
        return self._cells.get(cell_id)

    def to_bytes(self) -> bytes:
        width = height = GRID_SIZE
        total_cells = width * height

        # Build address intern table — one entry per unique renter
        renter_to_idx: dict[str, int] = {}
        renter_list: list[str] = []
        for cell in self._cells.values():
            if cell.renter is not None and cell.renter not in renter_to_idx:
                renter_to_idx[cell.renter] = len(renter_list)
                renter_list.append(cell.renter)

        # Section 1: raw 20-byte Ethereum addresses
        address_table = bytearray()
        for addr in renter_list:
            address_table += bytes.fromhex(addr.removeprefix("0x"))

        # Section 2: color presence bitmap + packed RGB (in cell_id order)
        bitmap = bytearray(math.ceil(total_cells / 8))
        packed_colors = bytearray()
        for cell_id, cell in sorted(self._cells.items()):
            if cell.color is not None:
                bitmap[cell_id >> 3] |= 1 << (cell_id & 7)
                packed_colors += bytes([cell.color.r, cell.color.g, cell.color.b])

        # Section 3: rental records — 9 bytes each, fixed size
        rental_records = bytearray()
        n_rented = 0
        for cell_id, cell in sorted(self._cells.items()):
            if cell.renter is not None:
                expires_ts = int(cell.expires_at.timestamp()) if cell.expires_at else 0
                # uint24 LE: low 2 bytes then high byte
                rental_records += struct.pack("<HB", cell_id & 0xFFFF, (cell_id >> 16) & 0xFF)
                rental_records += struct.pack("<I", expires_ts)
                rental_records += struct.pack("<I", renter_to_idx[cell.renter])
                n_rented += 1

        meta_offset = 20 + len(address_table) + len(bitmap) + len(packed_colors)

        header = (
            _MAGIC
            + struct.pack("<HHIII", width, height, len(renter_list), n_rented, meta_offset)
        )

        return bytes(header + address_table + bitmap + packed_colors + rental_records)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Grid":
        if data[:4] != _MAGIC:
            raise ValueError(f"bad magic: {data[:4]!r}")

        width, height, n_renters, n_rented, meta_offset = struct.unpack_from("<HHIII", data, 4)
        total_cells = width * height
        pos = 20

        # Section 1: address table — n_renters × 20 bytes
        renter_list: list[str] = []
        for _ in range(n_renters):
            renter_list.append("0x" + data[pos : pos + 20].hex())
            pos += 20

        # Section 2: color bitmap + packed RGB
        bitmap_size = math.ceil(total_cells / 8)
        bitmap = data[pos : pos + bitmap_size]
        pos += bitmap_size

        colors_by_cell: dict[int, RGB] = {}
        color_pos = pos
        for cell_id in range(total_cells):
            if bitmap[cell_id >> 3] & (1 << (cell_id & 7)):
                r, g, b = data[color_pos], data[color_pos + 1], data[color_pos + 2]
                colors_by_cell[cell_id] = RGB(r=r, g=g, b=b)
                color_pos += 3

        # Section 3: rental records — 9 bytes each, at meta_offset
        rentals_by_cell: dict[int, tuple[str, datetime]] = {}
        pos = meta_offset
        for _ in range(n_rented):
            cell_id = struct.unpack_from("<HB", data, pos)
            cell_id = cell_id[0] | (cell_id[1] << 16)
            (expires_ts,) = struct.unpack_from("<I", data, pos + 3)
            (renter_idx,) = struct.unpack_from("<I", data, pos + 7)
            rentals_by_cell[cell_id] = (
                renter_list[renter_idx],
                datetime.fromtimestamp(expires_ts, tz=timezone.utc),
            )
            pos += 11

        grid = cls()
        for cell_id in colors_by_cell.keys() | rentals_by_cell.keys():
            renter, expires_at = rentals_by_cell.get(cell_id, (None, None))
            grid._cells[cell_id] = Cell(
                color=colors_by_cell.get(cell_id),
                renter=renter,
                expires_at=expires_at,
            )
        return grid
