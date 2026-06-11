# polyplace-watcher — behavioral specification

This document states what the watcher does — its observable behavior and the
invariants any implementation must preserve. It is the yardstick for the
legibility refactor: code not required by some sentence here is accidental
complexity and a candidate for deletion.

## 1. Purpose

The watcher maintains a queryable, live-streamable replica of the full
1000×1000 cell grid of a single deployed instance of the PlaceGrid contract
on one chain — local Anvil, testnet, or mainnet, whichever `GRID_ADDRESS`
points at. One process, one contract instance, the whole grid.

The chain is the sole source of truth. Everything the watcher holds — the
in-memory grid, the snapshot file — is a disposable cache. Losing all of it
costs only the time to re-scan the chain, never correctness.

## 2. The core invariant

> Applying an event overwrites cell fields with values taken entirely from the
> event, never reading-and-modifying a field it sets. Therefore replaying any
> in-order suffix of the event history converges to the same state.

Two event types exist, both keyed by `cell_id`:

- `CellRented(cell_id, renter, expires_at)` — overwrites renter and expiry;
  color is preserved.
- `CellColorUpdated(cell_id, renter, color)` — overwrites color; renter and
  expiry are preserved.

How the contract emits them (verified against `PlaceGrid.sol`): every rent
emits `CellRented` immediately followed by `CellColorUpdated` in the same
transaction (adjacent log indices); `CellColorUpdated` also occurs alone, via
`setColor`, which requires an active rental. Consequence: **a rented cell
always has a color** once its transaction is fully applied — a
renter-without-color state can exist only transiently, between applying the
two events of one rent.

This single property licenses every overlap and resume mechanism in the
system:

1. Resume re-scans the block of the last applied event (not block + 1).
2. The backfill/live seam can deliver the same event twice.
3. A client may interleave a `/grid` fetch with `/ws` deltas in any order.

**Scope of the invariant:** the deployed contract is immutable, so new event
semantics cannot appear under a running watcher — this is not a runtime
concern. It matters only when a *new contract version* is deployed, which is
a new world anyway: new `GRID_ADDRESS`, fresh snapshot path, watcher
cutover. The checklist item for that day: if the new contract has any
read-modify-write event (e.g. a color *blend*), the replay-convergence
property fails and every overlap mechanism above silently corrupts state —
the watcher's resume architecture must be rethought, not just pointed at the
new address.

## 3. Boundary

Data crosses the process boundary through exactly these channels:

**In:**

- Chain events over HTTP RPC (`eth_getLogs`, used for backfill).
- Chain events over a WebSocket subscription (used for live tailing).
- In both cases, **only `CellRented` and `CellColorUpdated`** are consumed.
  The contract emits other events on the same address
  (`OwnershipTransferred`, `RentPriceUpdated`, `RentDurationUpdated`) —
  these must be ignored, not decoded, and must never disturb the watcher.
- The snapshot file (read once at startup, if present).
- Environment variables (see §7).

**Out:**

- `GET /grid` — the full grid in a custom binary format (gzip, ETag/304).
  **Frozen contract** with the frontend; see §5.
- `WS /ws` — a stream of per-cell JSON deltas. **Frozen contract**; see §5.
- `GET /health` — JSON: `last_block`, `last_log_index`, `git_sha`,
  `watcher_alive`. Returns **200 while the watch task is alive, 503 once it
  has died** — Fly's HTTP check reads only the status code, so the 503 makes
  Fly restart the machine: Fly is the supervisor (decision D1).
- The snapshot file (written periodically and on graceful shutdown).
- Structured logs (see §8).

## 4. Lifecycle

**Cold start.** Load the snapshot if one exists. Open the live WebSocket
subscription *before* backfilling (so no event can fall between backfill and
live). Backfill from `max(START_BLOCK, snapshot.last_block, scanned_to + 1)`
in chunks, then consume the live subscription.

**Serving during backfill.** The HTTP/WS API serves from the moment the
process starts; there is no readiness gate — confirmed intentional (decision
D4). A client connecting mid-backfill sees a partial grid that fills in —
correctly, because of §5.

**Deltas have one path.** Every applied event — backfilled or live — flows
through the same store and is broadcast to all `/ws` subscribers. A frontend
connected across a watcher outage receives the missed deltas as a burst when
the watcher recovers.

**Checkpointing.** Periodically, if the grid changed, write the snapshot
(JSON: cells + last applied block/log-index). Also write it on graceful
shutdown. The snapshot exists purely to shorten the next backfill. The
*intrinsic* requirement is that restart cost stays bounded as the chain
grows; the cadence (currently 60 s) is an incidental constant — any value
that keeps re-scan time tolerable is fine.

**Failure policy: retry forever.** Any failure of the chain connection — WS
drop, RPC error mid-backfill, anything — is caught, logged, and retried. The
watch loop must never die from an exception; only cancellation stops it.
After reconnect, the resume rule above closes the gap. The *intrinsic*
requirements are "never die" and "don't hammer a struggling RPC" (bounded
backoff); the curve (currently 0.5 s doubling, capped at 60 s) is an
incidental constant.

**Kill at any point.** The process may be killed without warning at any
moment. The worst case is losing un-checkpointed progress and redoing
backfill work on restart. No client-visible state can be corrupted (§1, §2).

## 5. The client protocol

The contract with the frontend, and the reason the API is correct:

> A client that (1) subscribes to `/ws`, then (2) fetches `/grid`, then
> (3) applies deltas as they arrive, converges to the head of the chain —
> regardless of how deltas interleave with the fetch, whether the watcher is
> mid-backfill, or whether the watcher restarts behind it.

This works because each `/ws` message carries the **complete post-event state
of one cell** (`{i, r, g, b, renter, expires_at}`) — not a diff — and
messages arrive in chain order. "Converges" means precisely: once the client
has applied the last delta affecting a cell, its state for that cell is
byte-for-byte what `/grid` would return for it — deltas that arrived during
the fetch and were already reflected in the fetched grid re-apply harmlessly,
because the final delta for any cell *equals* that cell's state at head (§2).

Supporting details, all frozen:

- `/grid` returns the custom binary format (magic `PLG\x01`: header, renter
  address intern table, color presence bitmap + packed RGB, fixed-size rental
  records), gzip-compressed, with a strong ETag derived from
  `(last_block, last_log_index)`. `If-None-Match` yields 304. *Frozen does
  not mean endorsed*: this spec freezes the format because the frontend
  consumes it, not because it is judged optimal. Revisiting it is a
  coordinated watcher+frontend change, out of scope for this refactor.
- `/grid` may serve bytes slightly older than its ETag claims while the grid
  moves during compression; the delta stream covers the difference.
- Encoding is **deterministic** — a pure function of logical grid state.
  Canonical renter-table order: first appearance, scanning cells in ascending
  `cell_id`. (Decoders are index-based and don't care; determinism makes two
  grids comparable by their bytes.)
- Renter addresses are serialized lowercase on both channels; `expires_at`
  travels as unix seconds (uint32 on the wire, JSON number on `/ws`).
- `/ws` emits a message only when the affected cell **has a color**. Since
  the contract pairs every `CellRented` with a `CellColorUpdated` in the same
  transaction (§2), this withholds nothing: at worst, a fresh rental's state
  is announced one log-index later, by the paired color event, which carries
  the full cell state. This gate is **load-bearing**, not merely harmless:
  the frontend assumes every delta carries `r`, `g`, `b` and would corrupt
  its pixel buffers on a colorless message.

## 6. Time-blindness

Wall-clock time never affects grid state. `expires_at` is data the watcher
relays, not behavior it implements: an expired rental keeps its renter and
color until a later event overwrites them. Interpreting expiry is the
frontend's job (rendering) and the contract's job (rentability). The only
clock in the watcher is the checkpoint cadence.

## 7. Configuration

| Variable | Status |
|---|---|
| `WEB3_HTTP_URL`, `WEB3_WS_URL` | Essential — differ local vs prod. |
| `GRID_ADDRESS` | Essential — the one contract being watched. |
| `START_BLOCK` | Essential — the contract's deployment block; the backfill horizon. |
| `CORS_ORIGINS` | Essential — the frontend origin(s) allowed to call the API. |
| `POLYPLACE_GIT_SHA` | Kept — deploy provenance surfaced in `/health`. |
| `SNAPSHOT_DIR` | Optional; directory holding the snapshot file, default `.` (Fly sets `/data`). The *filename* is always derived: `snapshot-{grid_address_lowercase}.json` (decision D6; replaces `SNAPSHOT_PATH`). |
| `SNAPSHOT_INTERVAL` | **Removed** — hardcoded at 60 s (decision D6). |
| `BACKFILL_CHUNK_SIZE` | Kept — RPC providers differ in `eth_getLogs` range limits (decision D6). |

**Operational invariant:** snapshot identity is coupled to contract identity.
A snapshot written for one `GRID_ADDRESS` must never be loaded for another —
otherwise cells from the old contract are resurrected. Deriving the path from
the address (decision D6) makes this mechanical instead of a `fly.toml`
comment.

## 8. Observability

The log stream must let an operator reconstruct the
**connection → backfill → live → snapshot** lifecycle after the fact:
connects, subscriptions, backfill chunk progress, connection losses (with
backoff and exception), snapshot saves/loads, startup config. Secrets in RPC
URLs are scrubbed.

Per-event and per-request logging is **not required** and should be removed.

## 9. Out of scope / accepted risks

- **Reorgs are unhandled — a purposeful gap.** What a reorg means here: the
  chain's last few blocks are replaced by a competing branch, so an event the
  watcher already applied may no longer exist on the canonical chain. The
  watcher has no rollback: the affected cell keeps showing the orphaned
  event's state until some later event overwrites that cell. The snapshot
  bakes the wrong fold in, so an ordinary restart does *not* heal it — only
  deleting the snapshot and re-scanning from `START_BLOCK` does (or the next
  rent of that cell). Some WS providers flag orphaned logs with
  `removed: true`; the watcher ignores the flag. Why this is accepted:
  Polygon PoS finality (milestones) is on the order of seconds, so reorgs
  are shallow and rare; the blast radius is one visually-wrong cell on a
  display whose stakes are nil; and proper handling (confirmation depth or
  rollback) would complicate the core invariant for negligible benefit.
  (Accepted — decision D3.)
- **No persistence of event history.** Only folded state is kept; the chain
  itself is the event log.
- **One contract per process.** Multi-grid support is a non-goal.

## Decision log

All open decisions were resolved on 2026-06-10. The sections above already
reflect them; this log records what was decided and why.

- **D1 — `/health` returns 503 when the watch task is dead.** Fly's existing
  check then fails and restarts the machine — supervision for free. *(Differs
  from current code, which returns 200 unconditionally.)*
- **D2 — `/ws` color-gating withholds nothing.** Verified against
  `PlaceGrid.sol`: every rent co-emits a color event in the same transaction,
  so the gate just defers announcing a rent by one log index (§2, §5).
- **D3 — reorgs are an accepted risk**, as narrated in §9. No code.
- **D4 — no readiness gate.** Progressive grid during backfill is a feature;
  the client protocol makes it correct (§4, §5). No code.
- **D5 — serialization duality is intentional.** Debuggable JSON for
  persistence, binary for the wire (§4, §5). No code.
- **D6 — config diet.** Derive the snapshot path from `GRID_ADDRESS`;
  hardcode the 60 s checkpoint cadence; keep `BACKFILL_CHUNK_SIZE` because
  RPC providers genuinely differ in range limits (§7). *(Differs from current
  code, which reads `SNAPSHOT_PATH` and `SNAPSHOT_INTERVAL` from env.)*
