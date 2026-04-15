default: fresh

# Tear down any previous run and spin up a clean environment
fresh:
    podman machine start 2>/dev/null || true
    podman compose down --remove-orphans
    podman compose up --build
