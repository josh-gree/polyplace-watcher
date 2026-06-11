"""The pure event fold (SPEC §2).

Two event types exist, both keyed by ``cell_id``. Applying an event
overwrites cell fields with values taken entirely from the event, never
reading-and-modifying a field it sets — therefore replaying any in-order
suffix of the event history converges to the same state. That single
property licenses every overlap and resume mechanism in the system.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CellRented:
    """Overwrites renter and expiry; color is preserved (SPEC §2)."""

    cell_id: int
    renter: str
    expires_at: int  # unix seconds


@dataclass(frozen=True)
class CellColorUpdated:
    """Overwrites color; renter and expiry are preserved (SPEC §2)."""

    cell_id: int
    renter: str
    color: tuple[int, int, int]


Event = CellRented | CellColorUpdated


@dataclass
class Cell:
    renter: str | None = None  # always lowercase (SPEC §5)
    expires_at: int | None = None  # unix seconds
    color: tuple[int, int, int] | None = None


class Grid:
    """The folded state: one Cell per cell_id an event has ever touched."""

    def __init__(self) -> None:
        self.cells: dict[int, Cell] = {}

    def apply(self, event: Event) -> Cell:
        cell = self.cells.setdefault(event.cell_id, Cell())
        if isinstance(event, CellRented):
            cell.renter = event.renter.lower()
            cell.expires_at = event.expires_at
        else:
            # The renter carried by a color event is not folded in: §2 says
            # the event overwrites color only; renter and expiry are preserved.
            cell.color = event.color
        return cell
