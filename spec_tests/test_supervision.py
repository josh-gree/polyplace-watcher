"""SPEC §3 / decision D1 — /health as the supervision signal.

    "Returns 200 while the watch task is alive, 503 once it has died —
    Fly's HTTP check reads only the status code, so the 503 makes Fly
    restart the machine: Fly is the supervisor."

A correct implementation retries forever (§4), so from the outside the
watch task never dies — the only way to test the 503 is to kill the task
directly. These tests therefore run the app in-process and use the single
sanctioned white-box hook: `app.state.watch_task` (see spec_tests/README).

No chain needed: the watcher pointed at an unreachable RPC is alive (§4),
which is exactly the distinction D1 draws — *retrying* is healthy,
*dead* is not.
"""

import importlib

import pytest
from starlette.testclient import TestClient

from .conftest import free_port, wait_until


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    """Import the app with a clean, chainless environment.

    SPEC README pin: "Importing the module must not require env vars (env
    is read at startup, not import)" — so import happens BEFORE the env is
    set, then startup config points at an unreachable RPC.
    """
    for var in (
        "WEB3_HTTP_URL",
        "WEB3_WS_URL",
        "GRID_ADDRESS",
        "START_BLOCK",
        "CORS_ORIGINS",
        "SNAPSHOT_DIR",
        "BACKFILL_CHUNK_SIZE",
        "POLYPLACE_GIT_SHA",
    ):
        monkeypatch.delenv(var, raising=False)

    import polyplace_watcher.app as app_module

    importlib.reload(app_module)  # prove import works with NO env at all

    dead = f"127.0.0.1:{free_port()}"  # nothing listens here
    monkeypatch.setenv("WEB3_HTTP_URL", f"http://{dead}")
    monkeypatch.setenv("WEB3_WS_URL", f"ws://{dead}")
    monkeypatch.setenv("GRID_ADDRESS", "0x" + "11" * 20)
    monkeypatch.setenv("START_BLOCK", "0")
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    return app_module


def test_app_module_imports_without_env(app_module):
    """The fixture already proved it: `import polyplace_watcher.app` with a
    scrubbed environment must not raise. Frozen by the deploy flows, which
    import the module before any per-machine env is meaningful.
    """
    assert hasattr(app_module, "app")


def test_health_is_200_while_alive_and_503_once_dead(app_module):
    """D1: 200 + watcher_alive=true while the watch task runs (even mid-
    backoff against a dead RPC — §4 retrying is healthy); 503 +
    watcher_alive=false once the task is dead, so Fly's status-code-only
    check fails and restarts the machine.
    """
    with TestClient(app_module.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["watcher_alive"] is True

        # Kill the watch task — the only externally-impossible failure.
        task = app_module.app.state.watch_task
        task.get_loop().call_soon_threadsafe(task.cancel)

        def _supervision_signal():
            r = client.get("/health")
            return r.status_code == 503 and r.json()["watcher_alive"] is False

        wait_until(_supervision_signal, timeout=10, message="/health -> 503")
