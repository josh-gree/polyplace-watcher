import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polyplace_watcher.grid import Grid


def fmt(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} MB"


data = Grid().to_bytes()
gz = gzip.compress(data, compresslevel=9)

print(f"raw:     {fmt(len(data))}  ({len(data):,} bytes)")
print(f"gzipped: {fmt(len(gz))}  ({len(gz):,} bytes)")
