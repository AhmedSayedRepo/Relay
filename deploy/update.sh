#!/usr/bin/env bash
# Pull the latest main, rebuild, apply migrations and restart.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
./deploy/dc.sh build backend
./deploy/dc.sh up -d db
./deploy/dc.sh run --rm backend python -m alembic upgrade head
# Score the new reader on every confirmed receipt before switching to it.
# Worse than the last accepted score: stop here, the running version stays.
# To accept a worse score on purpose: sudo ACCEPT_READER=1 ./deploy/update.sh
mkdir -p /var/lib/relay/eval && chmod 777 /var/lib/relay/eval
accept=()
if [ "${ACCEPT_READER:-}" = "1" ]; then accept=(--force); fi
if ! ./deploy/dc.sh run --rm -v /var/lib/relay/eval:/eval backend \
  python -m relay.infrastructure.cli eval-reader --file /eval/reader-score.json "${accept[@]}"; then
  echo "Update stopped: the new reader reads confirmed receipts worse (see above)."
  echo "Nothing was restarted. Send the lines above to fix it."
  exit 1
fi
./deploy/dc.sh up -d
# Rebuild and (re)start the long-running reader once it is enabled (model present).
if [ -f model/reader-config.json ]; then
  ./deploy/dc.sh --profile reader build reader
  ./deploy/dc.sh --profile reader up -d reader
fi
./deploy/schedule.sh
./deploy/dc.sh ps
