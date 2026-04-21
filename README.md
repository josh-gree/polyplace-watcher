# polyplace-watcher

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

## Logging

The service emits structured JSON logs to stdout and `logs/polyplace-watcher.log`.
Runtime configuration is logged as provided.

The app logger is set to `DEBUG`, so all project logs are written to both sinks.

Example:

```sh
uvicorn polyplace_watcher.app:app
```
