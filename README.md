# polyplace-watcher

## Logging

The service emits structured JSON logs to stdout and `logs/polyplace-watcher.log`.
Runtime configuration is logged as provided.

The app logger is set to `DEBUG`, so all project logs are written to both sinks.

Example:

```sh
uvicorn polyplace_watcher.app:app
```
