import gzip
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polyplace_watcher.events import GRID_SIZE, CellColorUpdated, CellRented
from polyplace_watcher.grid import Grid

TOTAL_CELLS = GRID_SIZE * GRID_SIZE
FILL_FRACTION = 0.10
N_CELLS = int(TOTAL_CELLS * FILL_FRACTION)
EXPIRES_AT = 1712345678

rng = random.Random(42)


def random_address() -> str:
    return "0x" + "".join(f"{rng.getrandbits(8):02x}" for _ in range(20))


def fmt(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} MB"


# Power-law renter distribution: 1000 unique addresses own all 100k cells
N_UNIQUE_RENTERS = 1_000
addresses = [random_address() for _ in range(N_UNIQUE_RENTERS)]
cell_ids = rng.sample(range(TOTAL_CELLS), N_CELLS)

grid = Grid()
for cell_id in cell_ids:
    addr = rng.choice(addresses)
    grid.apply(CellRented(cell_id=cell_id, renter=addr, expires_at=EXPIRES_AT))
    grid.apply(CellColorUpdated(cell_id=cell_id, renter=addr, color=rng.getrandbits(24)))

data = grid.to_bytes()
gz = gzip.compress(data, compresslevel=9)

print(f"cells filled:    {N_CELLS:,} / {TOTAL_CELLS:,} ({FILL_FRACTION:.0%})")
print(f"unique renters:  {N_UNIQUE_RENTERS:,}")
print()
print(f"raw:     {fmt(len(data))}  ({len(data):,} bytes)")
print(f"gzipped: {fmt(len(gz))}  ({len(gz):,} bytes)")
