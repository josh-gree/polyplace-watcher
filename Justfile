default:
    @just --list

# Start a clean local Anvil chain only.
local-chain:
    podman machine start 2>/dev/null || true
    podman compose down --remove-orphans
    podman compose up --detach anvil

# Start the watcher, using .local/watcher.env as a Compose override when present.
watcher:
    if test -f .local/watcher.env; then podman compose --env-file .local/watcher.env up --build watcher; else podman compose up --build watcher; fi

# Run the frontend Worker locally (from ../polyplace-frontend).
local-frontend:
    cd ../polyplace-frontend && npm run worker:dev

# Tear down local compose services.
down:
    podman compose down --remove-orphans

# Invoke polyplace-dev CLI with env populated from .local/watcher.env.
polyplace-dev *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f .local/watcher.env ]; then
        echo "error: .local/watcher.env not found — see README for how to populate it" >&2
        exit 1
    fi
    set -a
    source .local/watcher.env
    set +a
    export POLYPLACE_RPC_URL=http://127.0.0.1:8545
    export POLYPLACE_TOKEN_ADDRESS="$TOKEN_ADDRESS"
    export POLYPLACE_FAUCET_ADDRESS="$FAUCET_ADDRESS"
    export POLYPLACE_GRID_ADDRESS="$GRID_ADDRESS"
    export POLYPLACE_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    uv run polyplace-dev {{ARGS}}
