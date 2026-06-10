"""SPEC §3, §5 — the HTTP/WS surface, exercised over a real chain.

One watcher service per module, fed by real transactions. Each test cites
the spec sentence it enforces. Cells used here are row 0 (cell_id == x);
other test modules use other rows so they can share one anvil session.
"""

import json

import httpx
import pytest
from websockets.sync.client import connect as ws_connect

from polyplace_watcher import decode_grid

from .conftest import ALLOWED_ORIGIN, GIT_SHA, wait_until

pytestmark = pytest.mark.integration

# Row 0 cells, one per concern, so tests stay independent.
CELL_HEALTH = (0, 0)      # primed before service start
CELL_GRID = (1, 0)
CELL_ETAG = (2, 0)
CELL_WS = (3, 0)
CELL_WS_RECOLOR = (4, 0)


@pytest.fixture(scope="module")
def service(chain, deployment, renter, tmp_path_factory):
    """A watcher with one primed event already on-chain (so readiness is
    observable via /health.last_block — SPEC §3)."""
    from .conftest import Service

    receipt = renter.rent(*CELL_HEALTH, color=(255, 255, 255))

    snapshot_dir = tmp_path_factory.mktemp("api-snapshots")
    svc = Service(
        env={
            "WEB3_HTTP_URL": chain.http_url,
            "WEB3_WS_URL": chain.ws_url,
            "GRID_ADDRESS": deployment.grid,
            "START_BLOCK": str(deployment.start_block),
            "CORS_ORIGINS": ALLOWED_ORIGIN,
            "SNAPSHOT_DIR": str(snapshot_dir),
            "POLYPLACE_GIT_SHA": GIT_SHA,
        },
        snapshot_dir=snapshot_dir,
    )
    svc.start()
    svc.wait_healthy()
    svc.wait_for_block(receipt["blockNumber"])
    yield svc
    svc.kill()


def cell_id(x: int, y: int) -> int:
    return y * 1000 + x


# --- /health -----------------------------------------------------------------


def test_health_reports_progress_and_identity(service):
    """SPEC §3: "GET /health — JSON: last_block, last_log_index, git_sha,
    watcher_alive." All four fields, correct types, git_sha echoing the
    POLYPLACE_GIT_SHA env var, watcher_alive true on a healthy service.
    """
    health = httpx.get(f"{service.url}/health").json()
    assert isinstance(health["last_block"], int)
    assert isinstance(health["last_log_index"], int)
    assert health["git_sha"] == GIT_SHA
    assert health["watcher_alive"] is True


# --- /grid -------------------------------------------------------------------


def test_grid_response_contract(service, renter):
    """SPEC §5: "/grid returns the custom binary format ... gzip-compressed,
    with a strong ETag." §3 pins the media type and no-cache revalidation
    (the browser's HTTP cache is the ETag consumer).

    The decoded body must contain the on-chain cell with lowercase renter
    and a real expiry (§5: lowercase on both channels; unix seconds).
    """
    receipt = renter.rent(*CELL_GRID, color=(12, 34, 56))
    service.wait_for_block(receipt["blockNumber"])

    response = httpx.get(f"{service.url}/grid")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-encoding"] == "gzip"
    assert "no-cache" in response.headers["cache-control"]
    assert response.headers["etag"]

    # httpx transparently gunzips, exactly like a browser.
    cell = decode_grid(response.content).cells[cell_id(*CELL_GRID)]
    assert cell.color == (12, 34, 56)
    assert cell.renter == renter.address.lower()
    assert isinstance(cell.expires_at, int) and cell.expires_at > 0


def test_etag_revalidation_cycle(service, renter):
    """SPEC §5: "a strong ETag derived from (last_block, last_log_index).
    If-None-Match yields 304." And after a new event the state (and so the
    ETag) must change: 304 stops being returned, the new ETag differs.
    """
    first = httpx.get(f"{service.url}/grid")
    etag = first.headers["etag"]

    revalidated = httpx.get(f"{service.url}/grid", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.content == b""

    receipt = renter.rent(*CELL_ETAG, color=(1, 1, 1))
    service.wait_for_block(receipt["blockNumber"])

    changed = httpx.get(f"{service.url}/grid", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag


def test_cors_allows_the_configured_frontend_origin(service):
    """SPEC §3/§7: CORS_ORIGINS names "the frontend origin(s) allowed to call
    the API". A browser at that origin must be allowed in, and must be able
    to read the ETag header (revalidation needs it).
    """
    response = httpx.get(f"{service.url}/grid", headers={"Origin": ALLOWED_ORIGIN})
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    expose = response.headers.get("access-control-expose-headers", "")
    assert "etag" in expose.lower()


# --- /ws ---------------------------------------------------------------------


def test_fresh_rent_yields_exactly_one_full_state_delta(service, renter):
    """SPEC §5: each /ws message carries "the complete post-event state of
    one cell ({i, r, g, b, renter, expires_at})", and the color-gate means a
    fresh rent (CellRented + CellColorUpdated in one tx, §2) produces exactly
    ONE message — the gate is load-bearing for the frontend.
    """
    with ws_connect(service.ws_url) as ws:
        receipt = renter.rent(*CELL_WS, color=(100, 101, 102))
        service.wait_for_block(receipt["blockNumber"])

        message = json.loads(ws.recv(timeout=10))
        assert message == {
            "i": cell_id(*CELL_WS),
            "r": 100,
            "g": 101,
            "b": 102,
            "renter": renter.address.lower(),
            "expires_at": message["expires_at"],  # checked for type below
        }
        assert isinstance(message["expires_at"], int) and message["expires_at"] > 0

        # The gated CellRented must NOT have produced a second message.
        with pytest.raises(TimeoutError):
            ws.recv(timeout=2)


def test_recolor_delta_preserves_rental_state(service, renter):
    """SPEC §5: deltas are full cell state, not diffs — a recolor message
    still carries the renter and expiry set by the earlier rent (§2:
    "overwrites color; renter and expiry are preserved").
    """
    rent_receipt = renter.rent(*CELL_WS_RECOLOR, color=(1, 2, 3))
    service.wait_for_block(rent_receipt["blockNumber"])

    with ws_connect(service.ws_url) as ws:
        recolor_receipt = renter.set_color(*CELL_WS_RECOLOR, color=(200, 201, 202))
        service.wait_for_block(recolor_receipt["blockNumber"])

        message = json.loads(ws.recv(timeout=10))
        assert message["i"] == cell_id(*CELL_WS_RECOLOR)
        assert (message["r"], message["g"], message["b"]) == (200, 201, 202)
        assert message["renter"] == renter.address.lower()
        assert isinstance(message["expires_at"], int) and message["expires_at"] > 0


def test_subscriber_only_sees_events_from_subscription_onwards(service, renter):
    """SPEC §5: /ws is a delta stream from now, not a replay — history comes
    from /grid. A client connecting after the CELL_WS rent must not receive
    messages for cells it didn't watch change.
    """
    health = httpx.get(f"{service.url}/health").json()
    assert health["last_block"] is not None  # plenty has happened by now

    with ws_connect(service.ws_url) as ws:
        with pytest.raises(TimeoutError):
            ws.recv(timeout=2)
