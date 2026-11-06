#!/usr/bin/env bash
# Install the trained receipt reader on the server.
#   sudo ./deploy/enable-reader.sh /home/ubuntu/relay-model.zip
set -euo pipefail
cd "$(dirname "$0")/.."
archive="${1:?Usage: sudo ./deploy/enable-reader.sh <relay-model.zip>}"
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }
repo="$(pwd)"

echo "==> Unpacking the model into $repo/model"
command -v unzip >/dev/null || apt-get install -y -qq unzip >/dev/null
rm -rf model && mkdir -p model
unzip -q "$archive" -d model
[ -f model/reader-config.json ] || { echo "reader-config.json missing in $archive"; exit 1; }
chmod -R a+rX model

echo "==> Building the reader image (first time downloads ~1.5 GB, takes a few minutes)"
./deploy/dc.sh build backend
./deploy/dc.sh --profile reader build reader

echo "==> Scheduling the reader every 5 minutes"
cat > /etc/cron.d/relay-reader <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root flock -n /run/relay-reader.lock $repo/deploy/dc.sh --profile reader run --rm reader >>/var/log/relay-reader.log 2>&1
CRON
chmod 644 /etc/cron.d/relay-reader

echo "==> Test run on up to 3 receipts"
./deploy/dc.sh --profile reader run --rm reader python -m relay.infrastructure.cli model-suggest --limit 3
echo "Done. Log: /var/log/relay-reader.log"
