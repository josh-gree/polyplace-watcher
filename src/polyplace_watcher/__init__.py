"""polyplace-watcher: a queryable, live-streamable replica of one deployed
PlaceGrid contract's 1000x1000 cell grid (SPEC §1).

The public surface is the pure core: the two events, the grid fold (SPEC §2),
and the frozen wire codec (SPEC §5). The service around them lives in
``polyplace_watcher.app``.
"""

from .core import Cell, CellColorUpdated, CellRented, Grid
from .wire import decode_grid, encode_grid

__all__ = ["Cell", "CellColorUpdated", "CellRented", "Grid", "decode_grid", "encode_grid"]
