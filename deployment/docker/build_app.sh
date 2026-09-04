#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# Serialize builds on the shared production host to avoid memory/CPU spikes.
exec 9>/tmp/lanshare-dependency-build.lock
flock -w 1200 9

python3 tools/deploy/dependency_images.py ensure
export LANSHARE_RUNTIME_BASE
export LANSHARE_FRONTEND_BASE
LANSHARE_RUNTIME_BASE="$(python3 tools/deploy/dependency_images.py ref runtime)"
LANSHARE_FRONTEND_BASE="$(python3 tools/deploy/dependency_images.py ref frontend)"
if [ "$#" -eq 0 ]; then
    set -- docker compose
fi

started="$SECONDS"
"$@" build app
echo "APPLICATION_BUILD_SECONDS=$((SECONDS - started))"
