#!/usr/bin/env bash
# Pull the latest main, rebuild, apply migrations and restart.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
./deploy/dc.sh build backend
./deploy/dc.sh up -d db
./deploy/dc.sh run --rm backend python -m alembic upgrade head
./deploy/dc.sh up -d
# Rebuild and (re)start the long-running reader once it is enabled (model present).
if [ -f model/reader-config.json ]; then
  ./deploy/dc.sh --profile reader build reader
  ./deploy/dc.sh --profile reader up -d reader
fi
./deploy/schedule.sh
./deploy/dc.sh ps
