"""SPEC §2 — the core invariant: events are per-field overwrites, and
replaying any in-order suffix of the event history converges.

Pure tests: no chain, no service. Exercises only the pinned core API
(`CellRented`, `CellColorUpdated`, `Grid`, `encode_grid`).
"""

import random

from polyplace_watcher import CellColorUpdated, CellRented, Grid, encode_grid

RENTER_A = "0x" + "aa" * 20
RENTER_B = "0x" + "bb" * 20


def fold(events) -> Grid:
    grid = Grid()
    for event in events:
        grid.apply(event)
    return grid


def test_rent_overwrites_rental_fields_and_preserves_color():
    """SPEC §2: "CellRented(cell_id, renter, expires_at) — overwrites renter
    and expiry; color is preserved."

    A re-rent of an already-colored cell must replace renter/expiry while the
    color survives untouched.
    """
    grid = fold([
        CellRented(cell_id=7, renter=RENTER_A, expires_at=1_000),
        CellColorUpdated(cell_id=7, renter=RENTER_A, color=(10, 20, 30)),
        CellRented(cell_id=7, renter=RENTER_B, expires_at=2_000),
    ])
    cell = grid.cells[7]
    assert cell.renter == RENTER_B
    assert cell.expires_at == 2_000
    assert cell.color == (10, 20, 30)


def test_color_update_overwrites_color_and_preserves_rental_fields():
    """SPEC §2: "CellColorUpdated(cell_id, renter, color) — overwrites color;
    renter and expiry are preserved."
    """
    grid = fold([
        CellRented(cell_id=7, renter=RENTER_A, expires_at=1_000),
        CellColorUpdated(cell_id=7, renter=RENTER_A, color=(10, 20, 30)),
        CellColorUpdated(cell_id=7, renter=RENTER_A, color=(99, 98, 97)),
    ])
    cell = grid.cells[7]
    assert cell.renter == RENTER_A
    assert cell.expires_at == 1_000
    assert cell.color == (99, 98, 97)


def test_fields_not_carried_by_any_event_yet_are_absent():
    """SPEC §2: overwrites take values "entirely from the event". A cell that
    has only ever seen a color event has no rental fields, and vice versa —
    nothing is invented.

    (Per §2 a colorless rented cell occurs only transiently, between the two
    events of one rent — this is that mid-transaction state.)
    """
    only_rented = fold([CellRented(cell_id=1, renter=RENTER_A, expires_at=1_000)])
    assert only_rented.cells[1].color is None

    only_colored = fold([CellColorUpdated(cell_id=2, renter=RENTER_A, color=(1, 2, 3))])
    assert only_colored.cells[2].renter is None
    assert only_colored.cells[2].expires_at is None


def test_renters_are_normalized_to_lowercase():
    """SPEC §5: "Renter addresses are serialized lowercase on both channels."

    The core normalizes case so every downstream channel agrees.
    """
    checksummed = "0xAaBbCcDdEeFf00112233445566778899aAbBcCdD"
    grid = fold([CellRented(cell_id=3, renter=checksummed, expires_at=1_000)])
    assert grid.cells[3].renter == checksummed.lower()


def test_double_apply_is_harmless():
    """SPEC §2: "The backfill/live seam can deliver the same event twice."

    Applying the identical event again must not change the grid.
    """
    events = [
        CellRented(cell_id=7, renter=RENTER_A, expires_at=1_000),
        CellColorUpdated(cell_id=7, renter=RENTER_A, color=(10, 20, 30)),
    ]
    once = fold(events)
    twice = fold(events + [events[-1]])
    assert encode_grid(once) == encode_grid(twice)


def test_replaying_any_in_order_suffix_converges():
    """SPEC §2: "replaying any in-order suffix of the event history converges
    to the same state."

    This is THE invariant — it licenses resume-from-last-block, the
    backfill/live seam, and the client's /grid + /ws interleaving (§2 lists
    all three). Checked exhaustively for every suffix of a generated history.
    """
    rng = random.Random(42)
    cells = [0, 1, 7, 999, 1_000, 70_000, 999_999]
    events = []
    for i in range(60):
        cell_id = rng.choice(cells)
        renter = rng.choice([RENTER_A, RENTER_B])
        if rng.random() < 0.4:
            events.append(
                CellRented(cell_id=cell_id, renter=renter, expires_at=1_000 + i)
            )
        else:
            color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            events.append(
                CellColorUpdated(cell_id=cell_id, renter=renter, color=color)
            )

    baseline = encode_grid(fold(events))
    for start in range(len(events)):
        replayed = encode_grid(fold(events + events[start:]))
        assert replayed == baseline, f"replay from suffix index {start} diverged"
