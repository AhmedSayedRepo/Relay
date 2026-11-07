# Releasing the Relay Agent APK

The `Android release` workflow builds a signed release APK and publishes it as a
GitHub Release with its SHA-256. The landing page's download button points at the
release asset.

## One-time setup

1. Create the signing key **on your own computer** and keep a backup. Losing it
   means phones cannot update the app; anyone holding it can sign updates.

   ```
   keytool -genkeypair -v -keystore relay-release.jks -alias relay \
     -keyalg RSA -keysize 4096 -validity 10000
   ```

2. In GitHub → the Relay repo → Settings → Secrets and variables → Actions:
   - Secret `RELAY_KEYSTORE_BASE64`: the keystore file as base64
     (`base64 -w0 relay-release.jks`, or on Windows
     `[Convert]::ToBase64String([IO.File]::ReadAllBytes("relay-release.jks"))`).
   - Secrets `RELAY_KEYSTORE_PASSWORD`, `RELAY_KEY_ALIAS` (`relay`),
     `RELAY_KEY_PASSWORD`.
   - Variable `RELAY_BASE_URL`: your server's HTTPS origin ending with `/`,
     e.g. `https://relay.example.com/`. The release app talks only to this server.
3. Public download (recommended, because this repo is private): create an empty
   **public** repo such as `relay-releases`, then add
   - Variable `RELAY_RELEASE_REPO` = `AhmedSayedRepo/relay-releases`
   - Secret `RELAY_RELEASE_TOKEN` = a fine-grained token with Contents: read and
     write on that repo only.

   Without these, the release is created in this private repo, which only
   collaborators can download.

## Each release

Push a tag (`git tag agent-v0.5.0 && git push origin agent-v0.5.0`) or run the
workflow manually with a version. The run's summary prints the APK link and its
SHA-256; put both in the landing page's Download settings.

Each release has two APKs: `relay-agent-<version>.apk` (receipts and bank SMS,
for the SMS phone) and `relay-agent-receipts-<version>.apk` (receipts only, no SMS
permission, so Play Protect does not flag it). Both use the same app id, so a
phone has one or the other.

The download link for the newest release can also be
`https://github.com/<release repo>/releases/latest/download/relay-agent-<version>.apk`.
