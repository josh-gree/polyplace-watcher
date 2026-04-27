# polyplace-watcher

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/josh-gree/polyplace-watcher)

## Frontend

This service only exposes the watcher API and WebSocket endpoints. It does not
serve frontend assets.

Run the frontend separately from `../polyplace-frontend`, using the Worker-local
dev setup when testing the full local topology.

## Local Backend

Docker Compose runs the backend side of the realistic local stack: Anvil on
`127.0.0.1:8545` and the watcher API/WebSocket service on `127.0.0.1:8000`.
The frontend Worker runs separately on `127.0.0.1:8787`, so local browser
requests still exercise CORS.

To start just the chain and watcher:

```sh
podman compose up anvil watcher
```

Compose includes deterministic contract address defaults for a fresh Anvil
deployment. To deploy contracts first and have the watcher use the generated
runtime config:

```sh
just local-chain
just deploy-local
just watcher
```

`just deploy-local` shells out to the sibling
[`polyplace-contracts`](../polyplace-contracts) repo and runs the Forge deploy
script against the host Anvil endpoint `http://127.0.0.1:8545`. It writes the
Forge deployment manifest to `.local/deployment.json` and derives
`.local/watcher.env` from it. The watcher container uses that file as a Compose
env override when it exists, and connects to Anvil through the Compose service
name: `http://anvil:8545` and `ws://anvil:8545`.

If the contracts repo is not present as a sibling, set
`POLYPLACE_CONTRACTS_REPO` to its repo root before running `just deploy-local`.

The generated `.local/deployment.json` and `.local/watcher.env` are local
runtime state and are ignored by git.

## Continuous Integration

GitHub Actions runs on every push to `main` and on every pull request. The
workflow lives at `.github/workflows/ci.yml` and has two jobs:

- `test` checks out this repo and `polyplace-contracts` as siblings, installs
  Foundry (pinned to `stable`) and `uv`, then runs `uv sync --frozen` and
  `uv run pytest`. The pytest suite spawns a local Anvil and deploys contracts
  via `forge script`, so both Foundry and the contracts repo are required.
- `docker-build` builds the production `Dockerfile` with Buildx and a GHA
  layer cache. The image is not pushed.

Local equivalents:

```sh
uv sync --frozen
uv run pytest        # needs `anvil` on PATH and ../polyplace-contracts checked out
podman build .
```

## Continuous Deployment

Merges to `main` are deployed to Fly.io automatically. The workflow lives at
`.github/workflows/deploy.yml` and is triggered via `workflow_run` after the
`CI` workflow finishes successfully on `main`, so a red CI run never produces
a deploy. It can also be run manually via `workflow_dispatch`.

The `deploy` job:

1. Checks out the exact SHA that CI tested.
2. Runs `flyctl deploy --remote-only`, which builds the image on Fly's remote
   builder using this repo's `Dockerfile` and `fly.toml`.
3. Hits `https://polyplace-watcher.fly.dev/health` and `/grid` as smoke
   checks. `/health` must return JSON containing `last_block` and
   `last_log_index` keys (values may be `null` immediately after a fresh
   deploy, before backfill catches up). `/grid` must return
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
