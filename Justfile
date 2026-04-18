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

# Start the watcher against the deployment in .local/watcher.env.
watcher:
    test -f .local/watcher.env || (echo 'Missing .local/watcher.env; run just deploy-local first.' && exit 1)
    podman compose up --build watcher

# Full backend flow: chain, deploy, watcher.
local-backend: local-chain deploy-local watcher

# Tear down local compose services.
down:
    podman compose down --remove-orphans
