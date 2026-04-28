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
deployment. To deploy contracts yourself and have the watcher use the
resulting addresses, run the deploy CLI from the
[`polyplace-contracts`](../polyplace-contracts) repo and translate its env
output into `.local/watcher.env`:

1. Start Anvil:

   ```sh
   just local-chain
   ```

2. In `polyplace-contracts/packages/python` (where the `polyplace-deploy`
   entry point is registered), run the deploy CLI against the host Anvil
   and capture the env block:

   ```sh
   cd ../polyplace-contracts/packages/python
   uv run polyplace-deploy \
     --rpc-url http://127.0.0.1:8545 \
     --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
     --env-out -
   ```

3. Translate the printed `POLYPLACE_*` vars into `.local/watcher.env` —
   `POLYPLACE_GRID_ADDRESS` becomes `GRID_ADDRESS`, `POLYPLACE_TOKEN_ADDRESS`
   becomes `TOKEN_ADDRESS`, `POLYPLACE_FAUCET_ADDRESS` becomes
   `FAUCET_ADDRESS`, `POLYPLACE_START_BLOCK` becomes `START_BLOCK`, and add
   `WEB3_HTTP_URL`, `WEB3_WS_URL`, `CORS_ORIGINS` by hand. Example:

   ```sh
   cat > .local/watcher.env <<EOF
   WEB3_HTTP_URL=http://anvil:8545
   WEB3_WS_URL=ws://anvil:8545
   START_BLOCK=<value of POLYPLACE_START_BLOCK>
   GRID_ADDRESS=0x...
   TOKEN_ADDRESS=0x...
   FAUCET_ADDRESS=0x...
   CORS_ORIGINS=http://127.0.0.1:8787,http://localhost:8787
   EOF
   ```

4. Start the watcher, which uses `.local/watcher.env` as a Compose env
   override when present and connects to Anvil through the Compose service
   name (`http://anvil:8545` / `ws://anvil:8545`):

   ```sh
   just watcher
   ```

The generated `.local/watcher.env` is local runtime state and is ignored by
git.

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
