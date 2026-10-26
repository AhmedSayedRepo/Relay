# Relay

Relay is a standalone, generic data collection platform. Its backend is designed to support multiple devices and data sources without requiring an AI agent, an AI service, or a particular messaging, banking, or payment provider.

Sprint 6 turns the existing collection and receipt pipeline into an operator product. The authenticated web application provides bounded dashboard totals, server-paginated transactions, protected receipt evidence, first-class duplicate investigation, review and processing queues, device/configuration visibility, reporting, formula-safe Excel export, and integrated Help. Existing local Tesseract OCR and deterministic, device-scoped reference duplicate identity remain unchanged. Relay sends no image or extracted text to an external AI service.

The [Sprint 6 delivery report](SPRINT_6_DELIVERY.md) records implementation, verification, permissions, and limitations. Earlier sprint reports are preserved as historical documents; statements in them about later sprints being unimplemented describe their original delivery points, not the current source tree.

## Architecture and repository

Dependencies point inward. Domain entities and rules do not import HTTP or persistence frameworks. Application use cases use ports for persistence, password hashing, and token handling. Infrastructure implements those ports. Presentation validates HTTP requests and delegates to use cases; the factory composes the application.

```text
relay/
├── src/relay/
│   ├── domain/           # Framework-independent entities, values, and rules
│   ├── application/      # Use cases, DTOs, and ports
│   ├── infrastructure/   # Settings, persistence, security, logging, admin CLI
│   └── presentation/     # FastAPI factory, dependencies, schemas, routes
├── migrations/           # Alembic environment and ordered revision scripts
├── tests/                # Isolated automated tests; optional PostgreSQL tests
├── android/              # Kotlin/Compose agent; domain, application, data, presentation
├── frontend/             # React/TypeScript/Vite operator application
├── docs/
│   ├── architecture.md
│   ├── sprint-2-architecture.md
│   ├── sprint-2-security.md
│   ├── sprint-2-proposal.md  # Historical Sprint 1 proposal
│   ├── sprint-3-proposal.md  # Historical Sprint 2 proposal
│   ├── sprint-3-architecture.md
│   ├── sprint-3-security.md
│   ├── sprint-3-security-scan/
│   ├── sprint-4-proposal.md  # Historical Sprint 3 proposal
│   ├── sprint-4-architecture.md
│   ├── sprint-4-security.md
│   ├── sprint-5-proposal.md  # Historical Sprint 4 proposal
│   ├── sprint-5-architecture.md
│   ├── sprint-5-security.md
│   ├── sprint-6-proposal.md  # Historical Sprint 5 proposal
│   ├── sprint-6-architecture.md
│   ├── sprint-6-security.md
│   ├── sprint-6-security-scan/
│   └── sprint-7-proposal.md  # Proposal only; not implemented
├── .github/workflows/ci.yml
├── .env.example
├── alembic.ini
├── compose.yaml
├── Dockerfile
├── pyproject.toml
├── requirements.lock
└── requirements-dev.lock
```

See [Sprint 6 architecture and API contracts](docs/sprint-6-architecture.md), [Sprint 6 security and privacy review](docs/sprint-6-security.md), and [the Sprint 7 proposal](docs/sprint-7-proposal.md). Sprint 7 is proposal-only.

## Prerequisites

- Python 3.12 or later for development; the container and CI use Python 3.12.
- Node.js 24 and npm 11 for local operator-frontend development; the container builds the pinned frontend dependencies in a separate Node stage.
- PostgreSQL 16, or Docker with Docker Compose v2 for the supplied local database.
- Tesseract 5 with explicit English and Arabic language-pack paths for the default receipt worker.
- A terminal that can accept a hidden password prompt when provisioning an administrator.
- For Android: JDK 21, Android SDK platform 36 and build-tools 36.0.0, and an Android device/emulator at API 26 or newer. Use the checked-in Gradle wrapper; target JVM bytecode is 17.

Run the commands below from this repository directory. `requirements.lock` contains pinned runtime dependencies; `requirements-dev.lock` includes the development tools. Update locks deliberately when updating dependencies. A lockfile does not replace dependency vulnerability review or base-image maintenance.

## Local Python setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
Copy-Item .env.example .env
```

On Linux/macOS, use `python3 -m venv .venv`, `source .venv/bin/activate`, and `cp .env.example .env` instead.

Install, test, and compile the operator frontend separately when developing outside Docker:

```text
cd frontend
npm ci
npm test
npm run lint
npm run build
```

After `frontend/dist` exists, the backend serves the single-page application at `/operator/`. The Dockerfile performs this build automatically. The access token stays in browser memory, so a refresh requires another sign-in.

Generate two independent random values locally, one for the database password and one for the JWT signing secret:

```text
python -c "import secrets; print(secrets.token_hex(32))"
```

Run the command twice. Set `POSTGRES_PASSWORD` and `JWT_SECRET` in `.env` to the respective values. Do not commit `.env`, paste secrets into tickets, or reuse sample values. Configure the host-run backend's database URL:

```dotenv
DATABASE_URL=postgresql+asyncpg://relay:<your-generated-database-password>@localhost:5432/relay
```

The angle-bracket text must be replaced. If choosing a password with reserved URL characters instead of the suggested hexadecimal generator, URL-encode that password in `DATABASE_URL`.

Start the local database and apply migrations explicitly:

```text
docker compose up -d db
python -m alembic upgrade head
python -m relay.infrastructure.cli create-admin --email admin@example.com
uvicorn relay.presentation.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

The administrator command prompts for a password and its confirmation without displaying either. There is no default administrator password, public registration endpoint, or committed bootstrap account. The CLI is an operator action that requires access to the configured database.

Use [liveness](http://127.0.0.1:8000/health) and [readiness](http://127.0.0.1:8000/ready) to inspect the running local service. Readiness checks database access; it is not a guarantee that migrations have been applied or that every future dependency is healthy.

## Docker setup

Copy `.env.example` to `.env` and set the required random secrets as described above. For the backend container, set the database hostname to `db`, not `localhost`:

```dotenv
DATABASE_URL=postgresql+asyncpg://relay:<your-generated-database-password>@db:5432/relay
```

```text
docker compose build backend
docker compose up -d db
docker compose run --rm backend python -m alembic upgrade head
docker compose run --rm backend python -m relay.infrastructure.cli create-admin --email admin@example.com
docker compose up -d backend
docker compose ps
```

Run the administrator command from an interactive terminal. Compose passes only the declared backend settings; the database password is provided separately to PostgreSQL. The backend runs as a non-root user with a read-only filesystem, temporary `/tmp`, and dropped Linux capabilities. The image healthcheck probes `/health`; `/ready` is available for dependency-aware checks. The optional `worker` profile uses a separate JWT secret, read-only object volume, internal-only network, no published port, and CPU, memory, and PID limits:

```text
docker compose --profile worker run --rm worker
```

The worker processes one bounded batch and exits. An operator or scheduler controls cadence.

Both published ports are bound to `127.0.0.1`. Port 5432 exists for local development and may be removed from Compose when only containers need the database. This is local development support, not an internet-ready production deployment.

```text
docker compose logs --tail 100 backend
docker compose stop
docker compose down
```

The named database and object-storage volumes survive `docker compose down`. Do not add `--volumes` unless you intend to permanently delete both. Changing `POSTGRES_PASSWORD` in `.env` does not change the password inside an already initialized PostgreSQL volume; manage an existing database password explicitly rather than deleting data to make configuration match.

## Configuration

Settings come from the environment and local `.env`; process environment variables take precedence. Missing required values or invalid settings fail startup. No signing secret or database connection string is embedded in application source. Treat database URLs as credentials as well as configuration.

| Variable | Default / requirement | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development`; `development`, `test`, `production` | Environment selection and configuration safeguards |
| `APP_NAME` | `Relay` | Service name in logs |
| `LOG_LEVEL` | `INFO` | Structured logging level |
| `DATABASE_URL` | Required | `postgresql+asyncpg://...` for normal operation; SQLite only for isolated tests |
| `JWT_SECRET` | Required; at least 32 bytes with diversity/placeholder checks | Independently generated signing secret |
| `JWT_ALGORITHM` | `HS256` only | Explicitly allowlisted signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15`; range `1..60` | Short-lived administrator access token |
| `DEVICE_ACCESS_EXPIRE_MINUTES` | `15`; range `1..60` | Short-lived device access token |
| `DEVICE_REFRESH_EXPIRE_DAYS` | `30`; range `1..90` | Absolute device refresh-family lifetime |
| `ENROLLMENT_EXPIRE_MINUTES` | `10`; range `1..10` | One-time enrollment challenge lifetime |
| `DEVICE_CONTACT_COALESCE_SECONDS` | `60`; range `1..300` | Minimum interval between unchanged contact writes |
| `JWT_ISSUER` | `relay` | Expected token issuer |
| `JWT_AUDIENCE` | `relay-admin` | Expected administrator token audience |
| `CORS_ORIGINS` | JSON `[]` | Browser origin allowlist; restrictive by default |
| `ALLOWED_HOSTS` | JSON `["localhost","127.0.0.1","testserver"]` | Accepted request hosts; production requires actual service hosts |
| `READINESS_TIMEOUT_SECONDS` | `3` | Upper bound on database readiness work |
| `DB_POOL_SIZE` | `5` | PostgreSQL connections maintained per process |
| `DB_MAX_OVERFLOW` | `5` | Additional connections per process under load |
| `DB_POOL_TIMEOUT_SECONDS` | `5` | Wait limit when the pool is exhausted |
| `POSTGRES_USER` | `relay` in Compose | Local PostgreSQL initialization user |
| `POSTGRES_DB` | `relay` in Compose | Local PostgreSQL initialization database |
| `POSTGRES_PASSWORD` | Required by Compose | Local PostgreSQL initialization password |
| `TEST_POSTGRES_URL` | Unset | Explicit disposable PostgreSQL database for opt-in integration tests |
| `RAW_EVENT_RETENTION_DAYS` | `30`; range `1..365` | Server retention period assigned to admitted raw events |
| `IMAGE_OBJECT_RETENTION_DAYS` | `14`; range `1..90` | Retention assigned to finalized image objects and linked events |
| `IMAGE_UPLOAD_SESSION_HOURS` | `24`; range `1..72` | Lifetime of incomplete upload sessions |
| `IMAGE_MAX_ACTIVE_UPLOADS` | `3`; range `1..10` | Concurrent active upload sessions allowed per device |
| `IMAGE_UPLOADS_PER_HOUR` | `30`; range `1..100` | Database-enforced initialization bound per device and rolling hour |
| `IMAGE_STORED_BYTES_PER_DEVICE` | `67108864`; range 8 MiB..1 GiB | Stored plus reserved binary quota per device |
| `OBJECT_STORAGE_ROOT` | `./relay-object-storage` | Filesystem adapter root; Compose uses a persistent named volume |
| `OCR_TESSERACT_EXECUTABLE` | Required absolute path for worker use | Fixed Tesseract executable; runtime `PATH` lookup is refused |
| `OCR_TESSDATA_DIR` | Required directory for worker use | Fixed language-pack directory |
| `OCR_LANGUAGES` | JSON `["eng","ara"]`; 1..4 unique lowercase codes | Requested languages recorded with each job and result |
| `OCR_TIMEOUT_SECONDS` | `20`; range `1..60` | Hard per-process OCR timeout |
| `PROCESSING_STALE_MINUTES` | `5`; range `1..60` | Age after which an interrupted job may be reclaimed |
| `PROCESSING_BATCH_LIMIT` | `10`; range `1..100` | Maximum jobs handled by one worker invocation |
| `OPERATOR_AUDIT_RETENTION_DAYS` | `365`; range `90..2555` | Retention assigned to duplicate decisions, safe retries, and export audit rows |
| `OPERATOR_FRONTEND_DIR` | `frontend/dist` | Compiled Vite directory served at `/operator/` when present |
| `WORKER_JWT_SECRET` | Required by Compose worker; independent random value | Satisfies shared fail-closed settings without exposing the backend signing secret |

Use JSON arrays for the CORS and host lists. An empty CORS list denies cross-origin browser access; it does not replace authentication and does not block non-browser clients. Wildcard CORS is rejected. Before `APP_ENV=production`, configure valid concrete hosts and permitted HTTPS browser origins, if any. Production rejects `localhost`, `127.0.0.1`, `testserver`, and DEBUG logging. Host entries contain no scheme, path, wildcard, or port; browser origins include the scheme and optional port but no trailing path. Keep `PYDANTIC_SETTINGS_DEBUG` disabled because raw settings-source diagnostics could expose secrets.

Do not publish `docker compose config` output with real environment values: interpolation can expose secrets. Invalid settings are redacted by the application, but shell history, infrastructure logs, and third-party tooling are separate trust boundaries.

## Migrations and database

The original `0001` through `0005` revisions remain unchanged. Migration `0006_operator_product` adds durable operator audit records and bounded operational-query indexes. Audit target UUIDs intentionally outlive shorter source retention. See [the Sprint 6 schema and retention notes](docs/sprint-6-architecture.md#operator-audit).

```text
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

Run migrations once as an explicit operator/release step before starting the service; workers do not create tables or run migrations on startup. For later schema changes, generate a revision with `python -m alembic revision --autogenerate -m "describe change"`, inspect the generated script, and test upgrades and rollbacks on a disposable database. Autogeneration is a draft, not a reviewed migration.

`python -m alembic downgrade base` removes all tables and their data. Downgrading `0003` discards raw events; downgrading `0002` discards enrollment, credentials, configuration, and acknowledgement history. These operations are appropriate for disposable CI checks, not a safe rollback plan for a populated environment. Backups and a reviewed data-preserving recovery procedure are required before production schema changes.

## API contract

| Method | Path | Authentication | Behavior |
| --- | --- | --- | --- |
| `GET` | `/health` | Public | Structured process liveness response |
| `GET` | `/ready` | Public | Database readiness; `503` when unavailable |
| `POST` | `/api/v1/auth/login` | Public | JSON email/password login; returns a short-lived JWT |
| `GET` | `/api/v1/auth/me` | Bearer token | Current active administrator |
| `POST` | `/api/v1/devices` | Bearer token | Create a device; `201` |
| `GET` | `/api/v1/devices` | Bearer token | List devices with bounded pagination |
| `GET` | `/api/v1/devices/{id}` | Bearer token | Retrieve one device; `404` when absent |
| `PATCH` | `/api/v1/devices/{id}` | Bearer token | Update explicitly supplied mutable fields |
| `POST` | `/api/v1/device/events/batch` | Device bearer token | Admit 1–20 generic events with independent per-item outcomes |
| `POST` | `/api/v1/device/objects/uploads` | Device bearer token | Initialize or reconcile an installation-bound image upload |
| `PUT` | `/api/v1/device/objects/uploads/{session_id}/chunks?offset=N` | Device bearer token | Append one binary chunk at the exact next offset; exact retries are idempotent |
| `POST` | `/api/v1/device/objects/uploads/{session_id}/finalize` | Device bearer token | Verify size, SHA-256, MIME and dimensions, finalize storage, and link the raw event |
| `GET` | `/api/v1/events` | Administrator bearer token | Bounded raw-event list with device, collector, and time filters |
| `GET` | `/api/v1/events/{event_id}` | Administrator bearer token | Retrieve one raw event |
| `DELETE` | `/api/v1/devices/{device_id}/events` | Administrator bearer token | Delete all raw events for one device and return a count |
| `GET` | `/api/v1/objects/{object_id}` | Administrator bearer token | Return object metadata without exposing its storage key |
| `GET` | `/api/v1/objects/{object_id}/content` | Administrator bearer token | Download with exact image type, safe attachment name, and `no-store` |
| `DELETE` | `/api/v1/objects/{object_id}` | Administrator bearer token | Delete the object and linked image event |
| `DELETE` | `/api/v1/devices/{device_id}/image-data` | Administrator bearer token | Erase the device's image objects, links, and incomplete sessions |
| `POST` | `/api/v1/objects/{object_id}/process` | Administrator bearer token | Idempotently enqueue an explicit processing version tuple |
| `GET` | `/api/v1/processing/jobs` | Administrator bearer token | List jobs with bounded status/object filters |
| `GET` | `/api/v1/processing/jobs/{job_id}` | Administrator bearer token | Inspect one job and its attempts |
| `GET` | `/api/v1/receipts` | Administrator bearer token | List candidates with bounded review/type/device filters |
| `GET` | `/api/v1/receipts/{candidate_id}` | Administrator bearer token | Inspect machine fields, OCR, duplicate link, and audit history |
| `POST` | `/api/v1/receipts/{candidate_id}/review` | Administrator bearer token | Accept, correct, or reject while preserving machine extraction |
| `GET` | `/api/v1/dashboard` | Administrator bearer token | Bounded operational totals with monetary values separated by currency |
| `GET` | `/api/v1/transactions` | Administrator bearer token | Server-side date/search/filter/sort/paginated transaction projection |
| `GET` | `/api/v1/transactions/{candidate_id}` | Administrator bearer token | Receipt, OCR, review, duplicate, source, and protected-image provenance |
| `GET` | `/api/v1/duplicate-groups` | Administrator bearer token | Paginated deterministic duplicate groups |
| `GET` | `/api/v1/duplicate-groups/{group_id}` | Administrator bearer token | Paginated canonical/member comparison evidence |
| `POST` | `/api/v1/duplicate-groups/{group_id}/members/{candidate_id}/resolution` | Administrator bearer token | Record a supplemental audited judgment without changing the deterministic link |
| `GET` | `/api/v1/review-queue` | Administrator bearer token | Paginated candidates requiring review |
| `POST` | `/api/v1/processing/jobs/{job_id}/retry` | Administrator bearer token | Audit and enqueue one eligible bounded transient retry |
| `GET` | `/api/v1/operations/devices` | Administrator bearer token | Device/configuration status without credentials |
| `GET` | `/api/v1/reports/summary` | Administrator bearer token | Bounded report summary |
| `GET` | `/api/v1/reports/export.xlsx` | Administrator bearer token | Audited, filtered, formula-safe Excel workbook |

All processing and receipt responses receive `Cache-Control: no-store`. Image finalization queues the default version tuple without running OCR in the request. Run a bounded batch separately:

```text
python -m relay.infrastructure.cli process-pending-images --limit 10
```

Login request:

```json
{"email": "admin@example.com", "password": "<your-admin-password>"}
```

Login response has `access_token`, `token_type` (`bearer`), and `expires_in` (seconds). Send the token in `Authorization: Bearer <token>` for protected routes. The current-user response contains `id`, `email`, `is_active`, and `created_at`; it never includes the password hash.

Device creation request:

```json
{
  "external_device_id": "device-lab-01",
  "name": "Lab device 01",
  "platform": "android",
  "app_version": "0.1.0",
  "status": "PENDING"
}
```

`external_device_id` is a case-sensitive administrator-managed identifier of 1–128 characters, not a phone number, and cannot be changed by PATCH. It is distinct from the Android installation UUID. Names are 1–120 characters. `platform` matches `[a-z][a-z0-9_-]{0,31}` and is immutable through PATCH. `app_version` is null or 1–64 characters. Device text must not contain control characters or leading/trailing whitespace. `status` is `ACTIVE`, `DISABLED`, or `PENDING`, with `PENDING` the create default. `id`, `created_at`, `updated_at`, and `last_seen_at` are server-owned fields. Sprint 2 updates contact state after authenticated device contact and coalesces unchanged contact writes.

PATCH accepts only `name`, `app_version`, and `status`. Omitting a field preserves its value. Explicit `null` clears `app_version`; it is invalid for required fields. Disable a device with `{"status":"DISABLED"}` rather than deleting it. Disabling also revokes enrolled credentials; subsequent authentication, configuration, acknowledgement, heartbeat, renewal, and event-ingestion requests fail. Setting the record back to `ACTIVE` does not revive revoked credentials. Explicit re-enrollment requires a new challenge.

`GET /api/v1/devices?limit=20&offset=0` returns `items`, `limit`, and `offset`. The default limit is 20 and allowed range is 1–100; offset is 0–1,000,000. No total count, filtering, or search contract is provided yet.

Errors share this structure, and response headers carry the request ID:

```json
{
  "error": {
    "code": "<stable-error-code>",
    "message": "<safe-message>",
    "request_id": "<request-id>"
  }
}
```

Validation errors use `422`, invalid/missing administrator authentication uses `401`, missing resources use `404`, conflicts use `409`, readiness dependency unavailability uses `503`, and unexpected failures use sanitized `500` responses. JSON bodies remain limited to 16 KiB; only the exact authenticated chunk route permits a 256 KiB binary body. Readiness has its own structured status/dependencies response. Rejected CORS preflights use the standard JSON error envelope. Clients may supply a valid `X-Request-ID` (1–64 ASCII letters, digits, dots, underscores, or hyphens, starting with a letter or digit); invalid or duplicate IDs are replaced. Never send credentials or sensitive content in a correlation header.

## Tests and quality checks

```text
python -m pytest
python -m ruff check .
python -m black --check .
python -m mypy src
```

The default test suite uses isolated test databases and must not use a production database. PostgreSQL tests are opt-in via `TEST_POSTGRES_URL`; without it those tests are skipped. The real OCR smoke is also opt-in and requires explicit `RELAY_TEST_TESSERACT` and `RELAY_TEST_TESSDATA` paths. SQLite tests prove application behavior but do not substitute for PostgreSQL-specific migration, constraint, and concurrency checks.

To exercise PostgreSQL integration locally, create a separate disposable database, set `TEST_POSTGRES_URL=postgresql+asyncpg://.../relay_test`, and run `python -m pytest -m postgres`. The database name must contain `test`; each case owns and drops a randomly named test schema. Do not point this setting to a shared development, staging, or production database. Run the full suite again with the setting present to include both isolated and PostgreSQL coverage.

GitHub Actions retains the backend lockfile, Ruff/Black/strict MyPy, PostgreSQL 16 migration roundtrip/drift checks, and the complete Pytest suite with PostgreSQL integration. A separate Android job installs JDK 21 and the Android SDK, builds debug and unsigned release variants, runs Android lint, unit tests, and Kotlin formatting checks. CI database credentials exist only for that disposable service; its signing secret is generated per run. The workflow does not deploy or sign a distribution release.

Commands in this README are instructions, not verification evidence. The delivery verification report records which checks were actually executed and any environment limits.

## Android setup and enrollment

Open `android/` in Android Studio or point `ANDROID_HOME` at an installed Android SDK. An untracked `android/local.properties` may instead contain `sdk.dir=<absolute SDK path>`. Do not commit machine paths or signing credentials.

Production/release builds use HTTPS and disallow cleartext traffic. The default `https://relay.invalid/` is intentionally inert. Set a real HTTPS backend URL with the Gradle project property `relayBaseUrl`; the value is an endpoint, never a credential:

```text
cd android
./gradlew --no-daemon -PrelayBaseUrl=https://relay.example.com/ assembleRelease
```

On Windows use `gradlew.bat`. Release output is unsigned; signing/provisioning and deployment are separate operator actions. Do not install a test signing key as a production key.

For a local Android emulator, keep Uvicorn bound to `127.0.0.1:8000`, add `10.0.2.2` to the backend's development `ALLOWED_HOSTS`, and build **only debug** with its explicit URL:

```dotenv
ALLOWED_HOSTS=["localhost","127.0.0.1","testserver","10.0.2.2"]
```

```text
cd android
./gradlew --no-daemon -PrelayBaseUrl=http://10.0.2.2:8000/ installDebug
```

The emulator maps `10.0.2.2` to the host loopback interface; its own `localhost` does not refer to the development machine. For a USB device, an optional local path is `adb reverse tcp:8000 tcp:8000` with a debug build using `http://127.0.0.1:8000/`. Only use a device you control and remove forwarding afterward with `adb reverse --remove tcp:8000`. Debug cleartext is restricted to loopback/emulator hosts; there is no trust-all TLS configuration. Production host allowlists must contain actual service hosts, not these development names. No LAN exposure is needed for these local paths.

1. Start the backend/database and apply migrations as above.
2. Log in as an administrator outside the Android app. Create an Android device using the existing device API.
3. Request `POST /api/v1/devices/{device_id}/enrollment-challenges` with administrator bearer authentication and `{"replace_existing":false}`. The returned one-time value expires after ten minutes by default.
4. Enter that value in the Android enrollment screen and submit. The app creates its own installation UUID and receives device credentials; administrator credentials never enter the app.
5. Use the status screen's manual synchronization action. Inspect `/api/v1/devices/{device_id}/state` with administrator authentication to see enrollment, contact, and configuration acknowledgement.
6. Publish a validated `collectors.sms` configuration through `PUT /api/v1/devices/{device_id}/configuration` with the current `expected_version` and `schema_version:1`.
7. On the device, explicitly enable local SMS consent and press the permission button. Remote enablement cannot override either local denial.

For images, publish a declarative `collectors.images` configuration containing `saf_tree` source IDs and bounded rules. The user must then enable local image consent and initiate **Select folder** in the app. Android's `ACTION_OPEN_DOCUMENT_TREE` picker grants a persisted read capability only for that chosen document-provider tree; Relay requests no broad media or storage permission. The approval timestamp is the initial watermark, and the last successful image upload is the upgrade watermark, so existing files are not imported again by default. After a successful upload, Relay records the installation ID, source ID, and content hash locally; the record survives app restarts and device reboots, expires after 180 days, and retains at most 500 entries. Retryable uploads stay queued and are not recorded as complete. Clearing consent or selecting/removing a source clears the observation records at that privacy boundary. Relay still collects changed bytes from the same source and different images that contain the same transaction reference. Clearing consent removes Relay's encrypted queued copies, while clearing the source also releases the persisted URI permission. API 26 is the minimum supported Android version; this SAF model applies across the supported API 26–36 range.

The [Sprint 3 architecture](docs/sprint-3-architecture.md) documents the collector, rules, envelope, queue, ingestion, retry, idempotency, and retention contracts. Do not copy live tokens or SMS content into shell history, logs, bug reports, screenshots, or request IDs.

Android verification commands (using default HTTPS base URL) are:

```text
cd android
./gradlew --no-daemon assembleDebug assembleRelease lint testDebugUnitTest ktlintCheck
```

Android-specific instrumentation requires a running emulator/device; `connectedDebugAndroidTest` is a separate command and does not run with the unit-test command above. Commands here are instructions; consult the Sprint 5 delivery evidence for the checks executed against the unchanged Android source.

## Security and operational limits

- All active administrator accounts can manage all devices. There is no tenant boundary or RBAC. Device authentication is a separate installation-bound protocol; it never grants administrator access.
- Passwords are hashed with Argon2id using explicit cost parameters in the security adapter. Authentication rechecks the user's active state; disable an administrator with `python -m relay.infrastructure.cli disable-user --email admin@example.com` to deny subsequent authenticated requests, including requests with previously issued access tokens.
- Administrator access tokens are short-lived; administrators have no refresh flow, individual token revocation, password reset, MFA, or public registration. Device access tokens are short-lived and use a separate signing domain/audience/type, rotating refresh credentials, and active-state checks on every request. Device disabling/revocation and renewal invalidate the relevant prior credentials.
- Structured application logs include service/environment/request correlation while avoiding passwords, bearer tokens, secrets, and request bodies. Uvicorn access logs are disabled in the supplied run commands to avoid URL/query leakage. Configure any proxy or hosting access logs separately so they also exclude sensitive data.
- The supplied Uvicorn commands do not trust forwarded headers. A future deployment must explicitly configure its trusted proxy boundary; do not blindly trust client-supplied forwarding headers.
- Before public exposure, a trusted reverse proxy must provide TLS termination, login/enrollment/renewal abuse controls, bounded request sizes, suitable timeouts, and secure access logging. Enrollment's per-challenge attempt limit is not a global rate limiter. These deployment controls are not implemented by the local Compose stack.
- Database backups, least-privilege production database roles, secret management/rotation, monitoring, and dependency/image vulnerability review remain operator responsibilities. Pool limits are per process and must be sized against database capacity when adding workers.
- Android credentials, SMS event payloads, approved source URI, and full queued image records use separate Android Keystore-backed protection and no-backup storage. This protects ordinary storage extraction, not a rooted, instrumented, unlocked, or otherwise compromised running device. There is no hardware attestation guarantee or remote wipe.
- `RECEIVE_SMS` remains the only personal-content runtime permission. Image access uses the user-driven Storage Access Framework and adds no manifest storage permission. The implementation receives new SMS messages and scans only direct children newly modified after folder approval; it has no historical inbox or media-library access.
- Image candidates are bounded to 8 MiB, 12,000 pixels per dimension, 20 million pixels, and an estimated 80 MiB decoded surface before a 2,400-pixel processing derivative is created. Tesseract uses fixed arguments, no shell, a filtered environment, an isolated temporary directory, a hard timeout, and bounded output. The local Android queue remains capped at 20 images, 32 MiB encrypted data, and seven days.
- OCR is native-code processing of attacker-controlled images. The Compose worker adds coarse CPU, memory, PID, filesystem, capability, and network limits, but production still needs a narrowly privileged database role, stronger sandboxing, and disposable per-job isolation. The supplied worker shares the application database role.

## Reference documentation

Design references include the [FastAPI JWT authentication guide](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/), [SQLAlchemy asyncio documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html), and [Pydantic Settings documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). Relay keeps its security and persistence implementations outside the domain instead of copying a framework tutorial's single-file layout.
