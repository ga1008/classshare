#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# Run after building the replacement image, before replacing/stopping the old
# app. Arguments are the deployment's Compose command, including its options.
if [ "$#" -eq 0 ]; then
    set -- docker compose
fi

previous_containers="$("$@" ps -a -q app)"
if [ -z "$previous_containers" ]; then
    echo "No previous app container; static seed is unnecessary for a new install"
    exit 0
fi
if [[ "$previous_containers" == *$'\n'* ]]; then
    echo "Expected one previous app container; refusing an ambiguous static export" >&2
    exit 1
fi

# The archive is streamed to a pure asset-only parser inside the new image.
# No application/DB imports, data mounts read, tar extraction or host staging.
echo "Seeding previous immutable Vite chunks before application cutover"
if ! docker cp "$previous_containers:/app/static/dist/assets/." - | \
    "$@" run --rm --no-deps -T --entrypoint python app tools/publish_static_assets.py --seed-vite-tar -; then
    echo "Previous static graph export/validation failed; refusing application cutover" >&2
    exit 1
fi
