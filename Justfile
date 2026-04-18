default:
    @just --list

# Start a clean local Anvil chain only.
local-chain:
    podman machine start 2>/dev/null || true
    podman compose down --remove-orphans
    podman compose up --detach anvil

# Deploy contracts to local Anvil and write .local/watcher.env.
deploy-local:
    uv run python scripts/deploy_local.py

# Start the watcher, using .local/watcher.env as a Compose override when present.
watcher:
    if test -f .local/watcher.env; then podman compose --env-file .local/watcher.env up --build watcher; else podman compose up --build watcher; fi

# Full backend flow: chain, deploy, watcher.
local-backend: local-chain deploy-local watcher

# Tear down local compose services.
down:
    podman compose down --remove-orphans
