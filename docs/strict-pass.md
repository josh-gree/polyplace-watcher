# Strict pass — code not required by SPEC.md

Every item below is a line (or block) of `src/` that no sentence in SPEC.md
requires, plus the two places where the spec deliberately leads the code
(decisions D1, D6). Source total at time of audit: **951 lines**. Estimated
deletable before any structural refactor: **~230 lines (~24%)**.

## A. Dead code — delete, zero behavior change

| What | Where | Lines | Evidence |
|---|---|---|---|
| `x`/`y` computed fields on both event models | `events.py:36-44, 52-60` | ~24 | Zero consumers in `src/` or `tests/`. |
| `_ws_w3` attribute | `watcher.py:53, 158, 251, 254` | ~5 | Written, never read in `src/`. Tests use it as a "connected" probe (`test_watcher.py:206,248,262`, `test_e2e.py:53`) — deleting it requires giving tests a deliberate connected-signal instead of private state. |
| `GridStore.get` | `grid_store.py:84-85` | 3 | Test-only convenience; production goes through `Grid.get` internally. Keep only if declared API. |

## B. Logging over spec §8 — remove per-event/per-request logs

SPEC §8 requires lifecycle reconstruction only: connect, subscribe, backfill
chunks, connection losses, snapshot save/load, startup config, scrubbed URLs.
Everything else goes:

| What | Where |
|---|---|
| `watcher_live_event_applied` (debug, per event) | `watcher.py:217-226` |
| `watcher_fetch_logs_started` (debug) + `watcher_fetch_logs_finished` (info — duplicates `watcher_backfill_chunk`) | `watcher.py:86-93, 105-113` |
| `grid_event_applied` (debug, per event) | `grid_store.py:73-82` |
| `compressed_snapshot_cache_hit/miss/stored` (debug, per request) | `grid_store.py:92-99, 103-106, 118-125` |
| `grid_response_not_modified` / `grid_response_returned` (debug, per request) | `app.py:135-143, 146-155` |

`observability.py` (94 lines) shrinks accordingly:

- **Drop the file handler** (`observability.py:78-84`) and `DEFAULT_LOG_FILE`.
  Fly captures stdout; the file handler only litters the repo/container with a
  `logs/` dir (one already sits in the repo root).
- **Drop `reset_logging` + the handler-marker machinery**
  (`observability.py:15, 47-51, 87-94`) — self-described as "mainly useful
  for tests"; tests can manage handlers themselves.
- **Raise the level to INFO** once the debug calls are gone.
- Keep: JSON-to-stdout formatter, `scrub_url`.

Estimated total for category B: **~150 lines** (log calls + observability
shrinkage), most of the measured 31% logging share.

## C. Spec leads code — the two decided changes

1. **D1:** `/health` returns 503 when `watch_task.done()` (`app.py:114-126`
   currently returns 200 unconditionally). Fly's existing check becomes the
   supervisor.
2. **D6:** derive the snapshot path from `GRID_ADDRESS`
   (e.g. `/data/snapshot-{address}.json`), hardcode the 60 s cadence; delete
   the `SNAPSHOT_PATH` / `SNAPSHOT_INTERVAL` env reads (`app.py:63-64`) and
   the `[env] SNAPSHOT_PATH` line + warning comment in `fly.toml`.
   Deployment note: prod's existing file is `/data/snapshot-amoy-0x16b6.json`
   — either match the derived name or accept one re-backfill.

## D. Structural — where "from the book" shape differs from current shape

Not deletions; judgment calls for the refactor proper:

- **`Watcher.watch()` is a 110-line method** (`watcher.py:144-254`) mixing
  four concerns: connection management, resume policy, backfill, live tail.
  The spec describes these as distinct lifecycle stages (§4, diagram 02);
  the code should read like the diagram.
- **Defensive duplication:** `save_snapshot` raises if `last_block is None`
  (`grid_store.py:131`) but both callers already guard the same condition
  (`app.py:43, 97`). Pick one owner for the rule.
- **Two counters, one job:** `connection_attempt` and `consecutive_failures`
  (`watcher.py:145-146`) — only consumed by logs/backoff; one counter with a
  reset-on-success suffices.
- **`Grid.from_bytes`** (`grid.py:103-153`, ~50 lines): consumed only by
  tests. *Recommend keeping* — it is the executable documentation of the
  frozen wire format (§5) and what makes the encoder testable — but it should
  be understood as reference decoder, not production path.

## E. Test-suite friction (heads-up, not spec violations)

Several tests pin implementation internals rather than spec behavior:
`watcher._ws_w3` as a connected-probe, `Grid._cells` direct access. These
will fight categories A and D; fixing them must follow the standing rule that
test changes loosen assertions rather than narrow the ingest scope.
