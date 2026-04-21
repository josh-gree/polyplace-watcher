default:
    @just --list

# Start a clean local Anvil chain only.
local-chain:
    podman machine start 2>/dev/null || true
    podman compose down --remove-orphans
    podman compose up --detach anvil

# Deploy contracts to local Anvil and write .local/watcher.env.
deploy-local:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p .local
    MANIFEST_PATH="$PWD/.local/deployment.json"
    export POLYPLACE_RPC_URL=http://127.0.0.1:8545
    export POLYPLACE_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    for _ in $(seq 1 30); do
        if curl -sf -o /dev/null -X POST -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"web3_clientVersion","params":[],"id":1}' \
            "$POLYPLACE_RPC_URL"; then break; fi
        sleep 0.2
    done
    OUTPUT=$(uv run python -m polyplace_watcher.forge_deploy \
        --rpc-url "$POLYPLACE_RPC_URL" \
        --private-key "$POLYPLACE_PRIVATE_KEY" \
        --manifest-path "$MANIFEST_PATH" \
        --cooldown 30)
    eval "$(echo "$OUTPUT" | python3 -c 'import json, sys; d = json.load(sys.stdin); [print(f"{k.upper()}_ADDRESS={d[k]}") for k in ("grid", "token", "faucet")]')"
    cat > .local/watcher.env <<EOF
    WEB3_HTTP_URL=http://anvil:8545
    WEB3_WS_URL=ws://anvil:8545
    START_BLOCK=0
    GRID_ADDRESS=$GRID_ADDRESS
    TOKEN_ADDRESS=$TOKEN_ADDRESS
    FAUCET_ADDRESS=$FAUCET_ADDRESS
    CORS_ORIGINS=http://127.0.0.1:8787,http://localhost:8787
    EOF
    cat .local/watcher.env

# Start the watcher, using .local/watcher.env as a Compose override when present.
watcher:
    if test -f .local/watcher.env; then podman compose --env-file .local/watcher.env up --build watcher; else podman compose up --build watcher; fi

# Full backend flow: chain, deploy, watcher.
local-backend: local-chain deploy-local watcher

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
        echo "error: .local/watcher.env not found — run 'just deploy-local' first" >&2
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
