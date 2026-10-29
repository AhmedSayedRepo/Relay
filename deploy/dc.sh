#!/usr/bin/env bash
# docker compose with the cloud overlay, from the repository root.
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose -f compose.yaml -f deploy/compose.cloud.yaml "$@"
