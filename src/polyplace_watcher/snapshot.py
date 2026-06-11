"""The snapshot file (SPEC §4): a disposable cache of the folded grid that
exists purely to shorten the next backfill.

Debuggable JSON on disk, binary on the wire — the duality is intentional
(decision D5). Because the chain is the sole source of truth (SPEC §1),
writes are atomic and an unreadable snapshot is treated as absent, never as
a reason to crash.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .core import Cell, Grid

log = logging.getLogger(__name__)


def save_snapshot(path: Path, grid: Grid, last_block: int, last_log_index: int) -> None:
    payload = {
        "last_block": last_block,
        "last_log_index": last_log_index,
        "cells": {
            str(cell_id): {"renter": cell.renter, "expires_at": cell.expires_at, "color": cell.color}
            for cell_id, cell in grid.cells.items()
        },
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)  # atomic: a kill mid-write never corrupts the snapshot (SPEC §4)


def load_snapshot(path: Path) -> tuple[Grid, int, int] | None:
    try:
        payload = json.loads(path.read_text())
        grid = Grid()
        for cell_id, fields in payload["cells"].items():
            grid.cells[int(cell_id)] = Cell(
                renter=fields["renter"],
                expires_at=fields["expires_at"],
                color=tuple(fields["color"]) if fields["color"] is not None else None,
            )
        return grid, payload["last_block"], payload["last_log_index"]
    except FileNotFoundError:
        return None
    except Exception:
        log.warning("snapshot_unreadable", extra={"path": str(path)}, exc_info=True)
        return None
