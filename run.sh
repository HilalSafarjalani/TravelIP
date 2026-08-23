#!/usr/bin/env bash
# Build the frontend (if needed) and run the TravelIP backend, which serves
# the built frontend as static files. Binds to 127.0.0.1 only -- this app
# runs 100% locally, never expose it beyond loopback.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$root/frontend/dist/index.html" ]; then
    echo "Building frontend..."
    (cd "$root/frontend" && npm install && npm run build)
fi

cd "$root"
exec "$root/.venv/bin/python" -m backend.main
