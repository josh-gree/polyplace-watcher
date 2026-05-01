# polyplace-watcher

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/josh-gree/polyplace-watcher)

## Frontend

This service only exposes the watcher API and WebSocket endpoints. It does not
serve frontend assets.

Run the frontend separately from `../polyplace-frontend`, using the Worker-local
dev setup when testing the full local topology.

## Local Backend

The realistic local stack runs three native processes (no Docker/Compose):

1. **Anvil** on `127.0.0.1:8545` — started from the contracts repo.
2. **Watcher** on `127.0.0.1:8000` — started from this repo.
3. **Frontend Worker** on `127.0.0.1:8787` — started from
   `../polyplace-frontend` via `npm run worker:dev`.

### Quick start (deterministic defaults)

For a throwaway chain where you don't care about the deployed addresses,
the watcher defaults to the deterministic addresses produced by a fresh
Anvil using the default Foundry mnemonic.

```sh
# Terminal 1 — start anvil in the contracts repo
cd ../polyplace-contracts
just anvil

# Terminal 2 — start the watcher
just watcher
```

For the full local topology, also start the frontend Worker in a third
terminal (`npm run worker:dev` from `../polyplace-frontend`).

### Custom deployment

If you want to deploy your own contracts and use the resulting addresses:

1. **Start anvil** in the contracts repo:

   ```sh
   cd ../polyplace-contracts
   just anvil
   ```

2. **Deploy contracts** and capture the env block:

   ```sh
   cd ../polyplace-contracts
   just deploy-local
   ```

   This prints `POLYPLACE_GRID_ADDRESS=0x...`, `POLYPLACE_START_BLOCK=...`,
   etc. to stdout.

3. **Start the watcher** in this repo, passing the deployed addresses:

   ```sh
   export GRID_ADDRESS=0x...          # from deploy-local output
   export START_BLOCK=...             # from deploy-local output
   just watcher
   ```

   If you prefer inline env vars:

   ```sh
   GRID_ADDRESS=0x... START_BLOCK=... just watcher
   ```

4. **Start the frontend** from `../polyplace-frontend`:

   ```sh
   cd ../polyplace-frontend
   npm run worker:dev
   ```

## Continuous Integration

GitHub Actions runs on every push to `main` and on every pull request. The
workflow lives at `.github/workflows/ci.yml` and has two jobs:

- `test` checks out this repo, installs Foundry (only for the `anvil` binary)
  and `uv`, then runs `uv sync --frozen` and `uv run pytest`. The pytest
  suite spawns a local Anvil and deploys the contracts in-process via the
  `polyplace_contracts.deploy` Python library, which is pulled in as a git
  dependency in `pyproject.toml`.
- `docker-build` builds the production `Dockerfile` with Buildx and a GHA
  layer cache. The image is not pushed.

Local equivalents:

```sh
uv sync --frozen
uv run pytest        # needs `anvil` on PATH
podman build .
```

## Continuous Deployment

Merges to `main` are deployed to Fly.io automatically. The workflow lives at
`.github/workflows/deploy.yml` and is triggered via `workflow_run` after the
`CI` workflow finishes successfully on `main`, so a red CI run never produces
a deploy. It can also be run manually via `workflow_dispatch`.

The `deploy` job:

1. Checks out the exact SHA that CI tested.
2. Runs `flyctl deploy --remote-only --build-arg GIT_SHA=<sha>`, which builds
   the image on Fly's remote builder using this repo's `Dockerfile` and
   `fly.toml`. The build-arg pins the deploy SHA into the image as the
   `POLYPLACE_GIT_SHA` env var.
3. Polls `/health` until the served `git_sha` field equals the SHA we just
   deployed (120s deadline), then asserts the rest of the response shape.
   The SHA pin is what makes the smoke meaningful: without it the check
   could pass against the previous release during the brief window before
   edge routing fully cuts over. `/health` must also contain `last_block`
   and `last_log_index` keys (values may be `null` immediately after a
   fresh deploy, before backfill catches up). `/grid` must return
   `application/octet-stream` with a non-empty body.
4. If the deploy step succeeded but the smoke checks failed, the workflow
   runs `flyctl releases rollback -y` to revert to the previous release. The
   job still fails so the alert fires; the rollback just shrinks the window
   of brokenness.

Concurrency is set to `deploy-watcher` with `cancel-in-progress: false`, so
deploys queue rather than cancel each other mid-flight.

### Caveats of auto-rollback

`flyctl releases rollback` reverts the *image*, not the volume. If a bad
release wrote a corrupted `snapshot.json`, the rolled-back binary will load
the same bad snapshot. Manual remediation (delete the snapshot or restore a
known-good copy) is still required in that case. None of the current code
paths overwrite the snapshot in a way that would corrupt it on startup, but
worth knowing.

### Secrets

GitHub Actions only needs one secret:

- `FLY_API_TOKEN` — a deploy-scoped Fly token. Mint with
  `fly tokens create deploy --expiry 8760h` and store under
  *Settings → Secrets and variables → Actions*. Rotate by minting a new
  token and replacing the secret; the old token can be revoked with
  `fly tokens revoke <id>` (look up the id with `fly tokens list`).

All runtime secrets live in Fly, **not** in GitHub. They are managed with
`fly secrets set` / `fly secrets list` and are never referenced by the
workflow. The watcher reads at startup:

- `WEB3_HTTP_URL` — JSON-RPC HTTP endpoint for historical backfill.
- `WEB3_WS_URL` — JSON-RPC WebSocket endpoint for live log subscription.
- `START_BLOCK` — block height to start indexing from.
- `GRID_ADDRESS` — deployed `PlaceGrid` contract address.

Public, non-sensitive runtime config (`SNAPSHOT_PATH`, `CORS_ORIGINS`) is
kept in `fly.toml` under `[env]` and is checked into the repo.

## Logging

The service emits structured JSON logs to stdout and `logs/polyplace-watcher.log`.
Runtime configuration is logged as provided.

The app logger is set to `DEBUG`, so all project logs are written to both sinks.

Example:

```sh
uvicorn polyplace_watcher.app:app
```
