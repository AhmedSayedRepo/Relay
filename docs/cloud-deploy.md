# Running Relay on a free cloud server

Relay runs on an Oracle Cloud **Always Free** virtual machine, so it stays online when your PC is off.

| Part | Where |
|---|---|
| Relay server, database, images, OCR worker, backups | Oracle Cloud VM (Ubuntu 24.04, Ampere A1: 2 cores, 12 GB RAM) |
| Address for phones | `https://<name>.duckdns.org/` (free; Caddy gets the HTTPS certificate) |
| Admin portal | `https://<name>.duckdns.org/operator/`, plus an optional `*.vercel.app` alias |

## 1. Create the server (Oracle Cloud, ~15 min)

1. Sign up at <https://www.oracle.com/cloud/free/> and pick a home region close to you. Oracle verifies a card but does not charge within the Always Free limits.
2. Optional but recommended: **Billing → Upgrade to Pay As You Go**. Oracle may reclaim Always Free VMs that stay idle for 7 days, and a quiet Relay can look idle. Usage within the free limits still costs nothing.
3. Go to **Compute → Instances → Create instance**:
   - Image: **Canonical Ubuntu 24.04**.
   - Shape: **Ampere → VM.Standard.A1.Flex**, **2 OCPU, 12 GB**.
   - Networking: keep **Assign a public IPv4 address** on.
   - SSH keys: **Generate a key pair** and download the private key.
4. Open the ports. Go to the instance → **Subnet** → **Security List** → **Add Ingress Rules**:
   - Source `0.0.0.0/0`, TCP, destination port `80`.
   - Source `0.0.0.0/0`, TCP, destination port `443`.
5. Make the IP permanent: **Instance → Attached VNICs → IPv4 addresses → Edit → Reserved public IP**. Then copy the public IP.

## 2. Free address (DuckDNS, ~3 min)

1. Sign in at <https://www.duckdns.org> with Google or GitHub.
2. Add a subdomain, for example `relay-ahmed`. Put the server's public IP in **current ip** and click **update ip**.

## 3. Install Relay (~15 min)

1. Connect to the server from Windows PowerShell:
   ```
   ssh -i C:\path\to\the-key.key ubuntu@<public-ip>
   ```
2. Copy the private repo to the server. Create a GitHub fine-grained token with **Contents: Read** on Relay only. Use the token as the password when git asks:
   ```
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/AhmedSayedRepo/Relay.git
   cd Relay
   ```
3. Install everything. This writes `.env` with new random secrets, starts Relay with HTTPS, and schedules the worker, SMS matching, clean-up and nightly backups:
   ```
   sudo bash deploy/install.sh relay-ahmed.duckdns.org relay-admin.vercel.app
   ```
   The second argument is the optional Vercel admin address. Omit it if you won't use one.
4. Create your admin:
   ```
   sudo ./deploy/dc.sh run --rm backend python -m relay.infrastructure.cli create-admin --email you@example.com
   ```
5. Open `https://relay-ahmed.duckdns.org/operator/` from any device and sign in.

## Everyday commands (run on the server, inside `~/Relay`)

| Task | Command |
|---|---|
| Update to the latest main | `sudo ./deploy/update.sh` |
| Status | `sudo ./deploy/dc.sh ps` |
| Logs | `sudo ./deploy/dc.sh logs backend --tail 50` |
| Backup now | `sudo ./deploy/backup.sh` |

Backups are written to `/var/backups/relay` and kept for 14 days. They protect against mistakes, not against losing the whole server, so download a copy now and then:

```
scp -i C:\path\to\the-key.key ubuntu@<public-ip>:/var/backups/relay/* D:\RelayBackups\
```

## Phones

Build the release APK with `RELAY_BASE_URL` = `https://relay-ahmed.duckdns.org/` (see [android-release.md](android-release.md)).
