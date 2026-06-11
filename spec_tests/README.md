# spec_tests — the executable spec

This suite was written **from SPEC.md alone**, with no reference to any
implementation. It is the acceptance contract for a clean-room rewrite:

> **The task: make `uv run pytest spec_tests` pass without modifying anything
> in `spec_tests/` or `SPEC.md`.** Everything under `src/polyplace_watcher/`
> may be rewritten freely.

## Pinned surface

The tests import only this much; everything else about internal structure is
the implementer's choice:

| Pinned | What |
|---|---|
| `from polyplace_watcher import CellRented, CellColorUpdated, Grid, encode_grid, decode_grid` | The pure core (SPEC §2, §5). Events take keyword args: `CellRented(cell_id=…, renter=…, expires_at=…)` with `expires_at` unix seconds (int); `CellColorUpdated(cell_id=…, renter=…, color=…)` with `color` an `(r, g, b)` tuple of ints. `Grid()` has `.apply(event)` and `.cells` (a mapping `cell_id -> cell` where each cell has `.renter` (lowercase str or None), `.expires_at` (int or None), `.color` (tuple or None)). `encode_grid(grid) -> bytes`, `decode_grid(bytes) -> Grid`. |
| `polyplace_watcher.app:app` | The ASGI app — frozen by the Fly/Justfile deploy flows. Importing the module must not require env vars (env is read at startup, not import). |
| `app.state.watch_task` | The running watch `asyncio.Task`, exposed for the D1 supervision test. The single sanctioned white-box hook. |
| Env vars | Exactly as SPEC §7: `WEB3_HTTP_URL`, `WEB3_WS_URL`, `GRID_ADDRESS`, `START_BLOCK`, `CORS_ORIGINS`, `BACKFILL_CHUNK_SIZE` (optional), `SNAPSHOT_DIR` (optional, default `.`), `POLYPLACE_GIT_SHA` (optional). |
| Snapshot file | `{SNAPSHOT_DIR}/snapshot-{grid_address_lowercase}.json` — JSON containing at least `last_block` (SPEC §4, §7/D6). |

## Layout

| File | Spec sections | Needs chain? |
|---|---|---|
| `test_core.py` | §2 fold semantics, replay convergence | no |
| `test_wire.py` | §5 frozen binary format (golden bytes, roundtrip, determinism) | no |
| `test_api.py` | §3/§5 `/grid`, `/ws`, `/health`, CORS, ETag/304 | yes |
| `test_protocol.py` | §5 subscribe → fetch → apply ⇒ converge | yes |
| `test_lifecycle.py` | §4 cold backfill, snapshot-on-shutdown, resume, outage recovery, retry-forever | yes |
| `test_supervision.py` | D1 `/health` 503, import-without-env | no |

Chain-backed tests spin up a private `anvil` (auto-skipped if `anvil` is not
on PATH) and deploy the real contract suite via `polyplace_contracts.deploy`.
They drive it with real transactions — `bulkRentCells` / `setColor` — so the
full path chain → watcher → API → client is exercised with zero mocks.

## Run

```sh
uv run pytest spec_tests          # full suite (anvil on PATH)
uv run pytest spec_tests -m "not integration"   # pure tests only
```

Note: the *current* `src/` predates several spec decisions (D1, D6) and the
canonical-encoding rule, so it is expected to fail parts of this suite. That
gap is the rewrite's worklist.
