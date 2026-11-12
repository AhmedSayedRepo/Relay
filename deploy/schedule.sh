#!/usr/bin/env bash
# Write Relay's scheduled jobs. Safe to re-run; update.sh calls it.
#   sudo ./deploy/schedule.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }
repo="$(pwd)"
dc="$repo/deploy/dc.sh"

# With the trained model, the long-running reader container does image reading,
# SMS matching and auto-confirm every minute. Without it, cron does the first two.
if [ -f model/reader-config.json ]; then
  work=""
else
  work="* * * * * root flock -n /run/relay-worker.lock $dc --profile worker run --rm worker >/dev/null 2>&1
* * * * * root flock -n /run/relay-sms.lock $dc run --rm backend python -m relay.infrastructure.cli verify-sms >/dev/null 2>&1"
fi

cat > /etc/cron.d/relay <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
$work
30 2 * * * root $dc run --rm backend python -m relay.infrastructure.cli purge-expired-events >/dev/null 2>&1
15 3 * * * root $repo/deploy/backup.sh >/var/log/relay-backup.log 2>&1
# Daily report: runs hourly, sends only at 23:59 Cairo time (handles summer time).
59 * * * * root $dc run --rm backend python -m relay.infrastructure.cli daily-report >>/var/log/relay-report.log 2>&1
CRON
chmod 644 /etc/cron.d/relay
rm -f /etc/cron.d/relay-reader  # replaced by the long-running reader container
echo "Schedule written to /etc/cron.d/relay"
