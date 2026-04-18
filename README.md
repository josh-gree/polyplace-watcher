# polyplace-watcher

## Frontend

This service only exposes the watcher API and WebSocket endpoints. It does not
serve frontend assets.

Run the frontend separately from `../polyplace-frontend`, using the Worker-local
dev setup when testing the full local topology.

## Logging

The service emits structured JSON logs to stdout and `logs/polyplace-watcher.log`.
Runtime configuration is logged as provided.

The app logger is set to `DEBUG`, so all project logs are written to both sinks.

Example:

```sh
uvicorn polyplace_watcher.app:app
```
