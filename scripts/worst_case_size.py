"""
Measure worst-case serialised size of a full Grid, with and without gzip.

Worst case for compression:
  - Every cell is rented and coloured
  - Every renter is unique (maximises address table and rental records)
  - Colors are random bytes (incompressible)
  - Renter addresses are random bytes (incompressible)
"""

import gzip
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polyplace_watcher.events import GRID_SIZE
from polyplace_watcher.grid import Grid
from polyplace_watcher.events import CellRented, CellColorUpdated

TOTAL_CELLS = GRID_SIZE * GRID_SIZE  # 1_000_000
EXPIRES_AT = 1712345678
rng = random.Random(42)


def random_address() -> str:
    return "0x" + "".join(f"{rng.getrandbits(8):02x}" for _ in range(20))


def random_color() -> int:
    return rng.getrandbits(24)


def fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


if __name__ == "__main__":
    print(f"Building worst-case grid ({TOTAL_CELLS:,} cells, all unique renters)...")

    grid = Grid()
    for cell_id in range(TOTAL_CELLS):
        addr = random_address()
        grid.apply(CellRented(cell_id=cell_id, renter=addr, expires_at=EXPIRES_AT))
        grid.apply(CellColorUpdated(cell_id=cell_id, renter=addr, color=random_color()))

    print("Serialising...")
    data = grid.to_bytes()
    raw_size = len(data)

    print("Compressing...")
    gz_data = gzip.compress(data, compresslevel=9)
    gz_size = len(gz_data)

    print(f"\n  {'raw':<22} {fmt(raw_size):>10}  ({raw_size:>12,} bytes)")
    print(f"  {'gzipped (level 9)':<22} {fmt(gz_size):>10}  ({gz_size:>12,} bytes)")
    print(f"\n  compression ratio: {raw_size / gz_size:.1f}x")
