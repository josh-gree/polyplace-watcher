default:
    @just --list

# Start the watcher natively against a local chain.
# All env vars are overridable:
#   WEB3_HTTP_URL, WEB3_WS_URL, GRID_ADDRESS, START_BLOCK, CORS_ORIGINS
watcher:
    WEB3_HTTP_URL="${WEB3_HTTP_URL:-http://127.0.0.1:8545}" \
    WEB3_WS_URL="${WEB3_WS_URL:-ws://127.0.0.1:8545}" \
    GRID_ADDRESS="${GRID_ADDRESS:-0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0}" \
    START_BLOCK="${START_BLOCK:-0}" \
    CORS_ORIGINS="${CORS_ORIGINS:-http://127.0.0.1:8787,http://localhost:8787}" \
    uv run uvicorn polyplace_watcher.app:app --host 0.0.0.0 --port 8000
