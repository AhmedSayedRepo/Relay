#!/usr/bin/env bash
# One-time setup of Relay on an Ubuntu cloud server (tested target: Oracle
# Cloud Always Free, Ubuntu 24.04, Ampere A1). Run from the repository root:
#   sudo bash deploy/install.sh relay-yourname.duckdns.org [relay-admin.vercel.app]
set -euo pipefail

domain="${1:?Usage: sudo bash deploy/install.sh <your-domain> [admin-portal-host]}"
portal="${2:-}"
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }
cd "$(dirname "$0")/.."
repo="$(pwd)"
owner="${SUDO_USER:-ubuntu}"

echo "==> Installing Docker"
export DEBIAN_FRONTEND=noninteractive
echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | debconf-set-selections
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 iptables-persistent >/dev/null
systemctl enable --now docker
usermod -aG docker "$owner"

echo "==> Opening ports 80 and 443 in the server firewall"
# Oracle's Ubuntu images reject new inbound traffic except SSH by default.
for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
  # shellcheck disable=SC2086
  iptables -C INPUT $rule -j ACCEPT 2>/dev/null || iptables -I INPUT 1 $rule -j ACCEPT
done
netfilter-persistent save >/dev/null

echo "==> Writing .env"
if [ -f .env ]; then
  echo "    .env already exists, keeping it."
else
  secret() { python3 -c "import secrets; print(secrets.token_hex(32))"; }
  db_password="$(secret)"
  hosts="\"$domain\""
  [ -n "$portal" ] && hosts="$hosts,\"$portal\""
  public_url="https://${portal:-$domain}/operator"
  sed -e "s|^APP_ENV=.*|APP_ENV=production|" \
      -e "s|^POSTGRES_PASSWORD=$|POSTGRES_PASSWORD=$db_password|" \
      -e "s|^DATABASE_URL=$|DATABASE_URL=postgresql+asyncpg://relay:$db_password@db:5432/relay|" \
      -e "s|^JWT_SECRET=$|JWT_SECRET=$(secret)|" \
      -e "s|^WORKER_JWT_SECRET=$|WORKER_JWT_SECRET=$(secret)|" \
      -e "s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=[$hosts]|" \
      -e "s|^OPERATOR_PUBLIC_URL=$|OPERATOR_PUBLIC_URL=$public_url|" \
      .env.example > .env
  printf '\n# Cloud server\nRELAY_DOMAIN=%s\n' "$domain" >> .env
  chmod 600 .env
  chown "$owner" .env
fi

echo "==> Building and starting Relay (this takes a few minutes the first time)"
./deploy/dc.sh build backend
./deploy/dc.sh up -d db
./deploy/dc.sh run --rm backend python -m alembic upgrade head
./deploy/dc.sh up -d

echo "==> Scheduling the receipt worker, SMS matching, daily report, clean-up and backups"
./deploy/schedule.sh

./deploy/dc.sh ps
cat <<DONE

Relay is starting at https://$domain/operator/
(the HTTPS certificate can take a minute on first start).

Next, create your admin account:
  cd $repo && sudo ./deploy/dc.sh run --rm backend python -m relay.infrastructure.cli create-admin --email you@example.com
DONE
