"""SPEC §5 — the client protocol, end to end:

    "A client that (1) subscribes to /ws, then (2) fetches /grid, then
    (3) applies deltas as they arrive, converges to the head of the chain —
    regardless of how deltas interleave with the fetch."

This is the marquee guarantee of the whole service. The test plays the
client exactly as the real frontend does (subscribe → fetch → apply), with
events deliberately landing BOTH before the fetch (so their deltas are
already reflected in the fetched grid and re-apply harmlessly) and after it.

Cells used here are row 1 (cell_id = 1000 + x); test_api.py owns row 0.
"""

import json

import httpx
import pytest
from websockets.sync.client import connect as ws_connect

from polyplace_watcher import decode_grid

pytestmark = pytest.mark.integration


def cell_id(x: int, y: int = 1) -> int:
    return y * 1000 + x


def drain_deltas(ws, quiet_seconds: float = 2.0) -> list[dict]:
    """Collect /ws messages until the stream goes quiet."""
    deltas = []
    while True:
        try:
            deltas.append(json.loads(ws.recv(timeout=quiet_seconds)))
        except TimeoutError:
            return deltas


def grid_as_state(data: bytes) -> dict[int, tuple]:
    """Project a /grid body to {cell_id: (renter, expires_at, color)}."""
    return {
        cid: (cell.renter, cell.expires_at, cell.color)
        for cid, cell in decode_grid(data).cells.items()
    }


def apply_delta(state: dict[int, tuple], delta: dict) -> None:
    """What SPEC §5 asks of a client: overwrite the cell with the delta's
    complete post-event state."""
    state[delta["i"]] = (delta["renter"], delta["expires_at"], (delta["r"], delta["g"], delta["b"]))


def test_subscribe_fetch_apply_converges_to_head(make_service, renter):
    """SPEC §5, the full guarantee, including the harmless-overlap clause:
    "deltas that arrived during the fetch and were already reflected in the
    fetched grid re-apply harmlessly, because the final delta for any cell
    equals that cell's state at head."
    """
    receipt = renter.rent(0, 1, color=(10, 10, 10))  # prime: makes readiness observable
    service = make_service()
    service.wait_for_block(receipt["blockNumber"])

    with ws_connect(service.ws_url) as ws:
        # Events between subscribe and fetch: deltas will arrive on the
        # socket AND already be inside the fetched grid (the overlap case).
        renter.rent(1, 1, color=(20, 20, 20))
        before = renter.set_color(0, 1, color=(11, 11, 11))
        service.wait_for_block(before["blockNumber"])

        baseline = grid_as_state(httpx.get(f"{service.url}/grid").content)

        # Events after the fetch: deltas are the client's only source.
        renter.rent(2, 1, color=(30, 30, 30))
        renter.set_color(1, 1, color=(21, 21, 21))
        after = renter.rent(0, 1, color=(12, 12, 12))  # re-rent of a colored cell
        service.wait_for_block(after["blockNumber"])

        for delta in drain_deltas(ws):
            apply_delta(baseline, delta)

    head = grid_as_state(httpx.get(f"{service.url}/grid").content)
    assert baseline == head, "client state diverged from head after applying deltas"


def test_final_delta_for_a_cell_equals_its_state_at_head(make_service, renter):
    """SPEC §5: "the final delta for any cell *equals* that cell's state at
    head" — the property that makes replay order irrelevant. Checked
    directly: the last delta seen for a cell matches /grid exactly.
    """
    receipt = renter.rent(5, 1, color=(1, 1, 1))
    service = make_service()
    service.wait_for_block(receipt["blockNumber"])

    with ws_connect(service.ws_url) as ws:
        renter.set_color(5, 1, color=(2, 2, 2))
        last = renter.set_color(5, 1, color=(3, 3, 3))
        service.wait_for_block(last["blockNumber"])
        deltas = [d for d in drain_deltas(ws) if d["i"] == cell_id(5)]

    assert deltas, "expected deltas for the recolored cell"
    final = deltas[-1]
    head_cell = decode_grid(httpx.get(f"{service.url}/grid").content).cells[cell_id(5)]
    assert (final["renter"], final["expires_at"], (final["r"], final["g"], final["b"])) == (
        head_cell.renter,
        head_cell.expires_at,
        head_cell.color,
    )
