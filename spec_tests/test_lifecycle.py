"""SPEC §4 — lifecycle: cold start, checkpointing, resume, failure policy.

These tests run the watcher through real restarts, kills, and chain-WS
outages (via the severable TCP proxy). Each cites the §4 sentence it
enforces. Cells used here are row 2 (cell_id = 2000 + x); rows 0-1 belong
to test_api.py / test_protocol.py.
"""

import json
import time

import httpx
import pytest
from websockets.sync.client import connect as ws_connect

from polyplace_watcher import decode_grid

from .conftest import read_snapshot, wait_until

pytestmark = pytest.mark.integration


def cell_id(x: int, y: int = 2) -> int:
    return y * 1000 + x


def grid_cell(service, x: int, y: int = 2):
    data = httpx.get(f"{service.url}/grid").content
    return decode_grid(data).cells.get(cell_id(x, y))


# --- cold start ----------------------------------------------------------------


def test_cold_start_backfills_history(make_service, renter):
    """SPEC §4: "Backfill from max(START_BLOCK, snapshot.last_block,
    scanned_to + 1)" — with no snapshot, that is START_BLOCK. Events emitted
    BEFORE the process ever starts must appear in /grid.
    """
    receipts = [
        renter.rent(0, 2, color=(50, 50, 50)),
        renter.rent(1, 2, color=(60, 60, 60)),
    ]
    service = make_service()
    service.wait_for_block(receipts[-1]["blockNumber"])

    assert grid_cell(service, 0).color == (50, 50, 50)
    assert grid_cell(service, 1).color == (60, 60, 60)


# --- checkpointing ---------------------------------------------------------------


def test_graceful_shutdown_writes_snapshot_at_derived_path(make_service, renter):
    """SPEC §4: "Also write it on graceful shutdown." SPEC §7/D6: the
    filename is always derived — snapshot-{grid_address_lowercase}.json in
    SNAPSHOT_DIR. The file must be JSON carrying at least last_block.
    """
    receipt = renter.rent(2, 2, color=(70, 70, 70))
    service = make_service()
    service.wait_for_block(receipt["blockNumber"])

    service.stop()  # SIGTERM — graceful

    path = service.snapshot_path()
    assert path.exists(), f"expected snapshot at derived path {path.name}"
    snapshot = read_snapshot(path)
    assert isinstance(snapshot["last_block"], int)
    assert snapshot["last_block"] >= receipt["blockNumber"]


def test_restart_resumes_and_catches_up(make_service, renter):
    """SPEC §4: the cold-start rule again, now WITH a snapshot — restart
    loads it and backfills the gap. Events emitted while the watcher was
    down must appear after restart, alongside pre-shutdown state.
    """
    before = renter.rent(3, 2, color=(80, 80, 80))
    service = make_service()
    service.wait_for_block(before["blockNumber"])
    service.stop()

    # The world moves on while the watcher is down.
    during = renter.rent(4, 2, color=(90, 90, 90))

    service.start()
    service.wait_healthy()
    service.wait_for_block(during["blockNumber"])

    assert grid_cell(service, 3).color == (80, 80, 80)  # from before shutdown
    assert grid_cell(service, 4).color == (90, 90, 90)  # emitted during downtime


def test_kill_without_snapshot_loses_nothing(make_service, renter):
    """SPEC §4: "The process may be killed without warning at any moment.
    The worst case is ... redoing backfill work on restart." §1: the chain
    is the source of truth; the snapshot is a disposable cache. SIGKILL
    (no snapshot written), restart, full state rebuilt from chain.
    """
    receipt = renter.rent(5, 2, color=(100, 100, 100))
    service = make_service()
    service.wait_for_block(receipt["blockNumber"])

    service.kill()  # SIGKILL — no chance to checkpoint
    assert not service.snapshot_path().exists()

    service.start()
    service.wait_healthy()
    service.wait_for_block(receipt["blockNumber"])
    assert grid_cell(service, 5).color == (100, 100, 100)


# --- failure policy --------------------------------------------------------------


def test_chain_ws_outage_recovers_and_streams_missed_deltas(make_service, ws_proxy, renter):
    """SPEC §4: "Any failure of the chain connection — WS drop, RPC error
    mid-backfill, anything — is caught, logged, and retried" and "A frontend
    connected across a watcher outage receives the missed deltas as a burst
    when the watcher recovers" (single delta path).

    The watcher's chain-WS runs through a TCP proxy. We sever it mid-live,
    emit events during the outage, then resume and require: (a) the missed
    event reaches a frontend client that stayed connected throughout, and
    (b) /grid converges — all without restarting the watcher.
    """
    receipt = renter.rent(6, 2, color=(110, 110, 110))
    service = make_service(ws_url=ws_proxy.ws_url)
    service.wait_for_block(receipt["blockNumber"])

    with ws_connect(service.ws_url) as frontend:
        # Prove the live path works through the proxy first.
        live = renter.set_color(6, 2, color=(111, 111, 111))
        service.wait_for_block(live["blockNumber"])
        first = json.loads(frontend.recv(timeout=10))
        assert first["i"] == cell_id(6)

        ws_proxy.sever()
        missed = renter.rent(7, 2, color=(120, 120, 120))  # lands during the outage
        ws_proxy.resume()

        # (a) the missed delta arrives on the still-connected frontend socket
        burst = json.loads(frontend.recv(timeout=30))
        assert burst["i"] == cell_id(7)
        assert (burst["r"], burst["g"], burst["b"]) == (120, 120, 120)

    # (b) and /grid has converged, same process, no restart
    service.wait_for_block(missed["blockNumber"])
    assert grid_cell(service, 7).color == (120, 120, 120)


def test_admin_events_on_the_grid_address_are_ignored(make_service, renter, owner):
    """SPEC §3: "only CellRented and CellColorUpdated are consumed. The
    contract emits other events on the same address (OwnershipTransferred,
    RentPriceUpdated, RentDurationUpdated) — these must be ignored, not
    decoded, and must never disturb the watcher."

    An admin event lands between two rents: it must produce no /ws delta,
    and the watcher must keep indexing afterwards. (OwnershipTransferred is
    exercised implicitly in every test here — it sits at the grid's
    deployment block, inside every backfill range.)
    """
    receipt = renter.rent(8, 2, color=(130, 130, 130))
    service = make_service()
    service.wait_for_block(receipt["blockNumber"])

    with ws_connect(service.ws_url) as ws:
        owner.send(owner.grid.functions.setRentPrice(11 * 10**18))
        after = renter.rent(9, 2, color=(140, 140, 140))
        service.wait_for_block(after["blockNumber"])

        message = json.loads(ws.recv(timeout=10))
        assert message["i"] == cell_id(9), "admin event must not produce a delta"
        with pytest.raises(TimeoutError):
            ws.recv(timeout=2)

    assert grid_cell(service, 9).color == (140, 140, 140)


def test_unreachable_rpc_never_kills_the_watcher(make_service, chain):
    """SPEC §4: "retry forever ... The watch loop must never die from an
    exception." With both chain URLs pointing at a closed port the service
    must still serve /health — alive, with nothing indexed — and stay that
    way. (D1: watcher_alive false + 503 is reserved for a DEAD task; a
    retrying task is alive.)
    """
    dead = "127.0.0.1:1"  # nothing listens on port 1
    service = make_service(http_url=f"http://{dead}", ws_url=f"ws://{dead}")

    def _alive_and_empty():
        health = service.health()
        return health and health["watcher_alive"] is True and health["last_block"] is None

    # Holds immediately and keeps holding while the backoff loop churns.
    assert wait_until(_alive_and_empty, timeout=10, message="alive with empty grid")
    time.sleep(3)
    assert _alive_and_empty(), "watcher died while retrying an unreachable RPC"

    # No blocks were ever processed: graceful shutdown must NOT write a
    # snapshot (SPEC §4: checkpoint "if the grid changed").
    service.stop()
    assert not service.snapshot_path().exists()
