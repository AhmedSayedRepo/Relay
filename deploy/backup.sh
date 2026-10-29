#!/usr/bin/env bash
# Nightly backup: database dump plus receipt images, kept for 14 days.
set -euo pipefail
cd "$(dirname "$0")/.."
dest=/var/backups/relay
stamp=$(date +%F)
umask 027
mkdir -p "$dest"
# Readable by the docker group (the admin user) so backups can be downloaded.
chgrp docker "$dest"
./deploy/dc.sh exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$dest/db-$stamp.dump"
volume=$(docker volume ls -q --filter label=com.docker.compose.volume=relay_object_data | head -n1)
if [ -n "$volume" ]; then
  docker run --rm -v "$volume":/data:ro -v "$dest":/out alpine:3.20 \
    tar czf "/out/images-$stamp.tgz" -C /data .
fi
chgrp docker "$dest"/*
find "$dest" -type f -mtime +14 -delete
