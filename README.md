# polyplace-watcher

## Frontend

This service only exposes the watcher API and WebSocket endpoints. It does not
serve frontend assets.

Run the frontend separately from `../polyplace-frontend`, using the Worker-local
dev setup when testing the full local topology.

## Local Backend

The local backend is intentionally multi-step so contract addresses are not
hardcoded in Docker Compose.

```sh
just local-chain
just deploy-local
just watcher
```

`just deploy-local` deploys contracts to the host Anvil endpoint
`http://127.0.0.1:8545` and writes `.local/watcher.env`. The watcher container
loads that file and connects to Anvil through the Compose service name:
`http://anvil:8545` and `ws://anvil:8545`.

The generated `.local/watcher.env` is local runtime state and is ignored by git.

## Logging

The service emits structured JSON logs to stdout and `logs/polyplace-watcher.log`.
Runtime configuration is logged as provided.

The app logger is set to `DEBUG`, so all project logs are written to both sinks.

Example:

```sh
uvicorn polyplace_watcher.app:app
```
