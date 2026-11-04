#!/usr/bin/env bash
# Capture reviewed receipts into the training set and pack them for Kaggle.
# Run on the server from the repository root:  sudo ./deploy/export-training.sh
set -euo pipefail
cd "$(dirname "$0")/.."
owner="${SUDO_USER:-ubuntu}"
home="$(getent passwd "$owner" | cut -d: -f6)"
command -v zip >/dev/null || apt-get install -y -qq zip unzip >/dev/null
cli=(./deploy/dc.sh run --rm backend python -m relay.infrastructure.cli)

echo "==> Capturing reviewed receipts (safe to repeat)"
for _ in 1 2 3 4 5; do "${cli[@]}" capture-training --limit 100; done

echo "==> Exporting images and labels"
rm -rf /tmp/relay-export && mkdir -p /tmp/relay-export && chmod 777 /tmp/relay-export
./deploy/dc.sh run --rm -v /tmp/relay-export:/export backend \
  python -m relay.infrastructure.cli export-training --out /export/relay-training-export --include-holdout

archive="$home/relay-training-export.zip"
rm -f "$archive"
(cd /tmp/relay-export && zip -qr "$archive" relay-training-export)
chown "$owner" "$archive"
rm -rf /tmp/relay-export
echo
echo "Train receipts:   $(wc -l < <(unzip -p "$archive" relay-training-export/train-manifest.jsonl))"
echo "Holdout receipts: $(wc -l < <(unzip -p "$archive" relay-training-export/holdout-manifest.jsonl 2>/dev/null || true))"
echo "Saved $archive"
