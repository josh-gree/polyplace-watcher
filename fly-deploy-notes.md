# fly.io deployment — trial run notes

Date: 2026-04-17. Author: josh-gree (with Claude Code).

## Goal

Prove out a fly.io deployment of the `polyplace-watcher` FastAPI service while keeping `anvil` local, with a Cloudflare quick tunnel bridging the fly-hosted watcher to the local chain. No real chain yet. Frontend out of scope (to be hosted separately). Snapshot persistence accepted as ephemeral.

## Repo changes left in place

- `Dockerfile` — added a default `CMD` so the image runs standalone outside `docker compose`:
  ```dockerfile
  CMD ["uv", "run", "uvicorn", "polyplace_watcher.app:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
- `fly.toml` — new. Primary region `lhr`, `internal_port = 8000`, `auto_stop_machines = false`, `min_machines_running = 1`, HTTP health check on `GET /grid`, `shared-cpu-1x` / 1GB after memory bump (see below).
- `.dockerignore` — added `fly.toml`.

App itself was not modified. `FRONTEND_DIR` unset on fly, so `app.py:153` skips the static mount.

## Commands run

```sh
brew install flyctl cloudflared websocat
fly auth login
fly launch --no-deploy --copy-config --name polyplace-watcher --region lhr
# anvil + contracts + paint stress-test, local:
docker compose up anvil
docker compose up --no-deps paint          # deploy_local.py then paint_random.py
# tunnel:
cloudflared tunnel --url http://localhost:8545
# secrets (tunnel URL + deterministic anvil contract addresses):
fly secrets set \
  WEB3_HTTP_URL=https://<tunnel>.trycloudflare.com \
  WEB3_WS_URL=wss://<tunnel>.trycloudflare.com \
  START_BLOCK=0 \
  GRID_ADDRESS=0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0 \
  TOKEN_ADDRESS=0x5FbDB2315678afecb367f032d93F642f64180aa3 \
  FAUCET_ADDRESS=0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512
fly deploy
fly scale memory 1024    # see "What we observed"
fly apps destroy polyplace-watcher --yes   # torn down at end of session
```

## What we observed

### Worked
- Image built and deployed cleanly; `fly deploy` was ~1 min end-to-end.
- Cloudflare quick tunnel reached anvil from fly (`eth_blockNumber` → `0x14` on probe).
- Watcher started, subscribed to `eth_subscribe('logs')` over `wss://` through the tunnel. `web3.py`'s `WebSocketProvider` handled the `wss://` scheme without changes.
- Reconnect loop in `watcher.py:101-187` handled TLS + Cloudflare fine.
- `/grid` returned 200 with `Content-Encoding: gzip`, ETag `"None.None"` initially (empty store).
- Once backfill got far enough, `grid_event_applied` logs streamed at high volume (block 138 had ~280 log indices). Events were landing in `GridStore`.

### First incident — OOM at 256MB during backfill
Both machines were OOM-killed while running the initial `eth_getLogs(from=0, to=latest)`:
```
Out of memory: Killed process 651 (uvicorn) total-vm:204412kB, anon-rss:134496kB
```
Cause: `paint_random.py` stress-tests anvil with large numbers of paints; the single-shot backfill response didn't fit in 256MB.

**Fix**: `fly scale memory 1024` — machines recovered, health checks passed, backfill eventually applied events.

**Underlying issue not fixed**: the backfill is unbounded (`watcher.py` logs `watcher_fetch_logs_started from_block=0 to_block=latest`). At scale, no VM size saves you — need chunked pagination.

### Second incident — `asyncio.queues.QueueFull` from web3.py persistent provider
During the scale-triggered restart of the old 256MB machine:
```
File ".../web3/providers/persistent/persistent.py", line 380, in _message_listener_callback
    self._request_processor._subscription_response_queue.put_nowait(...)
asyncio.queues.QueueFull
```
Diagnosis: shutdown-time race. Our lifespan cancelled the consumer task; web3.py's background listener kept pushing new WS messages into its internal subscription queue with no consumer, and eventually the queue filled. Benign during shutdown (process was exiting anyway), but the same failure mode could in principle bite a live machine if paint events arrive faster than backfill drains. Not observed on the surviving 1GB machine during the session.

### What didn't verify cleanly — WebSocket passthrough
- `websocat wss://polyplace-watcher.fly.dev/ws` connected but received zero frames on the first probe (backfill hadn't produced store updates yet, so `GridStore.subscribe()` queue was empty).
- By the time fly logs showed `grid_event_applied` events streaming, the websocat connection had been killed by our timeout wrapper; a second probe via the Monitor tool timed out before we captured frames.
- We did not conclusively see paint events on the WS client from outside fly. The server code (`app.py:135-150`) is correct; the gap is purely in our verification — needs a longer-lived websocat session to confirm end-to-end.

### Other things worth noting
- `fly launch` created **two** machines for HA even with `min_machines_running = 1`. For a single stateful indexer this is redundant (both backfill, both subscribe, both OOM in lockstep). We didn't scale back to 1 before tearing down. Future runs: `fly scale count 1` after launch, or accept the doubling.
- Health check path `/grid` is cheap (etag/cache hit) once warm, but semantically wrong — fly is checking a business endpoint that does serialization work. A dedicated `/health` (or TCP check) would be better.
- fly health check fires every 15s from `172.19.x.x` (fly internal proxy IPs) — accounted for all the mystery `GET /grid` traffic in logs.
- `fly launch` rewrote `fly.toml` in its own style (single-quoted, added `memory_mb` alongside `memory`). Kept.
- GitHub secret `FLY_API_TOKEN` was auto-added to the repo by `fly launch`. Unused by us; can be revoked in repo settings.

## Follow-ups (not done)

1. **Paginate backfill in `src/polyplace_watcher/watcher.py`** — chunk `eth_getLogs` by block range (e.g. 1000 blocks at a time). The real fix for the 256MB OOM; also bounds memory regardless of chain history.
2. **Add `GET /health`** with no store access, and switch fly's HTTP check to it.
3. **Drop HA to one machine** — set `min_machines_running = 1` explicitly and run `fly scale count 1`, or accept HA doubling.
4. **Investigate the web3.py `QueueFull` during backfill-vs-live contention.** If backfill is slow and paint is fast, the subscription queue could fill during normal operation, not just shutdown.
5. **Named Cloudflare tunnel** once a domain is on Cloudflare — quick tunnel URL rotates on every `cloudflared` restart, requiring a `fly secrets set` round-trip.
6. **Re-run the WS verification end-to-end** with a session long enough to span backfill completion and live event emission.

## Repo artifacts

- `Dockerfile` (modified) — `CMD` appended.
- `fly.toml` (new).
- `.dockerignore` (modified) — `fly.toml` added.
- This file.

None of the app code under `src/polyplace_watcher/` was touched.

---

# Second trial run — end-to-end with frontend

Date: 2026-04-17 (afternoon, same day). Author: josh-gree (with Claude Code).

## Goal

Stand the whole loop up: fly-hosted watcher + Cloudflare-Workers-hosted frontend + local anvil proxied via Cloudflare quick tunnel. No stress test this round (pipeline smoke test first, then we pushed).

Topology we ended up with:

```
browser  ──HTTPS──▶  polyplace-frontend.joshuadouglasgreenhalgh.workers.dev   (frontend on CF Workers)
                         │ fetch /grid, ws /ws
                         ▼
                     polyplace-watcher.fly.dev   (watcher on fly, lhr)
                         │ wss + https
                         ▼
                     amazing-fed-equation-dating.trycloudflare.com   (quick tunnel)
                         │
                         ▼
                     localhost:8545   (anvil on mac)
```

## What changed in the repo this round

- **`scripts/fund.py` (new)**: one-shot dev funding. Queries `token.balanceOf(faucet)` and claims the full balance (1B tokens from `polyplace_contracts.deploy.INITIAL_SUPPLY`), then approves `MaxUint256` to the grid. No args. Separated from paint so you fund once and paint many.
- **`scripts/paint_random.py` (modified)**: added `--count N` flag (defaults to full 1M grid). Removed faucet + approve logic (now in `fund.py`). Hardcoded `gas = 300_000` — the old `estimate_gas(0, 0, ...)` call reverts with `CellNotAvailable` once (0,0) has ever been rented.
- **`src/polyplace_watcher/app.py` (modified)**:
  - Added `CORSMiddleware`. Origins come from `CORS_ORIGINS` env var (comma-separated, default `*`). Methods restricted to `GET`, `ETag` in `expose_headers` so `If-None-Match` round-trips from the browser.
  - Added `GET /health` returning `{"last_block": int|null, "last_log_index": int|null}`. Cheap, no store compression — for fly health checks and operator visibility.
- **`fly.toml` (modified)**: health check path `/grid` → `/health`. Memory kept at `256mb` (no stress test planned, though we did run one — see below).

## Commands run this round

```sh
# app stand-up (fly.toml already existed from first run)
fly launch --no-deploy --copy-config --name polyplace-watcher --region lhr --yes
fly secrets set \
  WEB3_HTTP_URL=https://amazing-fed-equation-dating.trycloudflare.com \
  WEB3_WS_URL=wss://amazing-fed-equation-dating.trycloudflare.com \
  START_BLOCK=0 \
  GRID_ADDRESS=0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0 \
  TOKEN_ADDRESS=0x5FbDB2315678afecb367f032d93F642f64180aa3 \
  FAUCET_ADDRESS=0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512 \
  --stage
fly deploy
fly scale count 1 --yes    # fly launch still spawns 2 for HA

# frontend-specific
fly secrets set CORS_ORIGINS=https://polyplace-frontend.joshuadouglasgreenhalgh.workers.dev --stage
fly deploy

# local dev iteration without rebuilding the image
podman compose run --rm --no-deps -v ./scripts:/app/scripts paint uv run python scripts/fund.py
podman compose run --rm --no-deps -v ./scripts:/app/scripts paint uv run python scripts/paint_random.py --count 1
```

## What we observed

### Worked
- Single-cell paint → event landed on fly within ~2s through the tunnel. `CellRented` + `CellColorUpdated` visible in `grid_event_applied` logs, ETag bumped, `/grid` updated.
- CORS preflight from the workers origin returned the expected headers (`access-control-allow-origin`, `access-control-expose-headers: ETag`).
- Frontend successfully fetched `/grid` and opened `/ws` from the workers domain.
- `/health` passes in ~100ms — good health-check target.

### Stress test attempt (1M cells)
Ran `paint_random.py --count 1000000` anyway. Two things broke:

1. **`/grid` response time went to 4–6 seconds per request.** Cause: `grid_store.py:63` (`self._cache_key = _UNSET` in `apply()`) invalidates the compressed-snapshot cache on *every* event. During the stress test the event rate outpaces any `/grid` consumer, so every request hits `cache_miss` and re-gzips the full grid. Logs showed `cache_miss → cache_stored` gaps of 4.3–6.3s consistently. The existing code comment at `grid_store.py:114-115` already acknowledges that stale-under-concurrency is acceptable — we're just not serving stale aggressively enough.
2. **Health check started flapping** (while still on `/grid`) because `/grid` was exceeding the 5s timeout. Moving the check to `/health` stopped the flapping immediately.

### Cloudflare quick tunnel — WS still silently drops
Hit the same failure mode as the first trial: after some idle period the `wss://` subscription stops delivering messages but web3.py doesn't see an error. `last_block` on `/health` freezes, HTTP requests through the same tunnel still work fine. Restart of the fly machine (forces watcher reconnect) is the manual recovery. This is the web3.py persistent-provider + CF-tunnel interaction; `watcher.py` has no idle detection.

### Chain-restart handling
When the user restarted anvil fresh mid-session, the fly watcher reconnected cleanly (because the tunnel URL hadn't changed) and backfilled 0 logs. No manual fly work needed as long as contracts end up at the same deterministic addresses (which `deploy_local.py` guarantees on a fresh chain + default mnemonic).

### Gotcha: `paint_random.py` gas estimation
The original script estimated gas via `rentCell(0, 0, 0xFF0000).estimate_gas(...)`. Once (0,0) is ever rented, this reverts with `CellNotAvailable` and the whole script dies before sending any tx. Silent-looking — the script just exits with a traceback. Replaced with a hardcoded `300_000`.

## Follow-ups (still not done)

Carrying over from the first run:

1. **Paginate backfill in `watcher.py`** — not hit this round because the fresh-chain backfills were tiny. Still required if you ever restart fly against a long-lived chain.
2. **WS idle-timeout / heartbeat in `watcher.py`** — we hit the silent-WS-drop again. The watcher needs to assume a WS is dead if it hasn't seen any message (including empty blocks) for N seconds, tear down, and reconnect. Without this, every tunnel hiccup is a manual `fly machine restart`.
3. **Throttle `/grid` cache recomputation in `grid_store.py`** — only regenerate the compressed snapshot every N seconds (say 1s) even if events have arrived. Serve slightly-stale bytes in between. The WS stream already communicates the live delta; `/grid` is the cold-start endpoint.
4. **Named Cloudflare tunnel** — quick tunnel URL still rotates on every cloudflared restart, so every restart is a `fly secrets set` round-trip.
5. **`docker-compose.yml` `paint` service command** is now broken — still runs `deploy_local.py && paint_random.py` but `paint_random.py` now requires funding to have run first. Update to `deploy_local.py && fund.py && paint_random.py --count 1000000` for the old stress-test behaviour.

## Repo artifacts (this round)

- `src/polyplace_watcher/app.py` — CORS middleware + `/health` endpoint.
- `scripts/fund.py` (new).
- `scripts/paint_random.py` — `--count` flag, funding removed, hardcoded gas.
- `fly.toml` — health check on `/health`, 256MB.

---

# Amoy deployment (2026-04-22)

## Goal

Deploy the watcher against the real Amoy testnet (chain `80002`) via Infura, with snapshot persistence on a Fly volume so restarts don't re-scan from block 0.

## Source of truth for addresses

`polyplace-contracts/deployments/amoy/2026-04-21-initial.json` — all three contracts deployed in block `0x2353184` = `37040516`.

| Secret | Value |
|---|---|
| `TOKEN_ADDRESS` | `0xe3adc914450953af784337120cf133c1c011414d` |
| `FAUCET_ADDRESS` | `0x8e5af8fdcef97be0821e7ee6493964dda7e07c59` |
| `GRID_ADDRESS` | `0xc0afc54f12cfb863ca6612bf79826410c9588fbc` |
| `START_BLOCK` | `37040516` |
| `WEB3_HTTP_URL` | `https://polygon-amoy.infura.io/v3/<KEY>` |
| `WEB3_WS_URL` | `wss://polygon-amoy.infura.io/ws/v3/<KEY>` |

## Repo changes (on `flyio` branch, rebased on `main`)

- `fly.toml` — added `[env] SNAPSHOT_PATH = '/data/snapshot.json'` and `[mounts] source = 'watcher_data', destination = '/data'`.
- No other code changes needed. `src/polyplace_watcher/config.py` already reads `WEB3_HTTP_URL` / `WEB3_WS_URL` / `START_BLOCK` / `{TOKEN,FAUCET,GRID}_ADDRESS` from env.

## Commands run

```sh
# App (previous trial destroyed it) and 1GB volume in lhr:
fly apps create polyplace-watcher
fly volume create watcher_data --size 1 --region lhr --yes -a polyplace-watcher
# → vol_491k5wqok92o2y5r, encrypted, scheduled snapshots on.
```

## Still to do

- [x] `fly secrets set` with the Amoy addresses + Infura URLs above. (All 6 staged; shown via `fly secrets list -a polyplace-watcher`.)
- [x] `fly deploy` from `flyio` branch (manual, one-shot) to verify Amoy e2e. (First deploy will apply the staged secrets automatically.)
- [x] Checksum-address fix in `src/polyplace_watcher/config.py` so forge-generated lowercase addresses don't crash `web3.py`.
- [x] Chunked backfill in `src/polyplace_watcher/watcher.py` — single `eth_getLogs` from `START_BLOCK` to `"latest"` hung against Infura for a ~1M-block range. `Watcher._backfill` now pins the upper bound via `eth_blockNumber` and iterates in `BACKFILL_CHUNK_SIZE` (default 10_000) slices, yielding events so `store.last_block` advances during catch-up.
- [x] `fly deploy` with chunked backfill and watch `/app/logs/polyplace-watcher.log` for `watcher_backfill_chunk` progress. Verified: 5 chunks processed in ~1s, 15 events applied, `last_block=37045693`, snapshot persisted to `/data/snapshot.json`.
- [x] Watch `/health` — `last_block` should climb from `37040516` toward current head. Verified: `{"last_block":37045693,"last_log_index":0}`.
- [ ] Once green, merge `flyio` → `main`. Deploys are manual via `fly deploy -a polyplace-watcher --remote-only` (no CI workflow).

