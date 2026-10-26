# Relay — Sprint 1 delivery

Relay's Sprint 1 backend foundation is implemented. The final suite passed **116 tests with no failures or skips**, including real PostgreSQL checks. A separate live Uvicorn/PostgreSQL smoke run passed all **10 HTTP checks**. Ruff, Black, and strict MyPy passed. **Sprint 2 is proposed only; no Android, enrollment, device credentials, synchronization, collectors, AI, or dashboard functionality was implemented.**

The workspace was initially empty and was not a Git repository. This delivery is a self-contained source repository export with CI configuration; no commit, push, deployment, or existing-project modification was performed.

## 1. Implementation summary

- Typed environment configuration with required secret values, restrictive CORS and hosts, and production safeguards.
- FastAPI application factory, lifespan-managed database engine, request-scoped asynchronous sessions, and explicit dependency wiring.
- Public liveness and database readiness endpoints, bounded readiness time, structured logs, request correlation, and safe JSON errors.
- Framework-independent domain entities, application use cases and ports, and separate SQLAlchemy persistence models.
- Administrator authentication using Argon2id password hashing and short-lived, claim-validated HS256 JWTs. Active account status is checked on protected requests.
- Interactive operator CLI to create and disable administrators, without a default account or public registration endpoint.
- Authenticated generic device creation, bounded listing, retrieval, metadata updates, and status management.
- Initial Alembic migration, dependency lockfiles, meaningful automated tests, quality-tool configuration, Docker/Compose support, and a GitHub Actions verification workflow.

## 2. Repository tree

```text
relay/
├── .github/workflows/ci.yml
├── .dockerignore
├── .env.example
├── .gitattributes
├── .gitignore
├── Dockerfile
├── compose.yaml
├── alembic.ini
├── pyproject.toml
├── requirements.lock
├── requirements-dev.lock
├── README.md
├── SPRINT_1_DELIVERY.md
├── docs/
│   ├── architecture.md
│   ├── sprint-2-proposal.md
│   └── verification-evidence.json
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/0001_users_and_devices.py
├── src/relay/
│   ├── domain/
│   │   ├── devices.py
│   │   ├── users.py
│   │   └── errors.py
│   ├── application/
│   │   ├── auth.py
│   │   ├── devices.py
│   │   ├── ports.py
│   │   └── errors.py
│   ├── infrastructure/
│   │   ├── cli.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── logging.py
│   │   ├── models.py
│   │   ├── repositories.py
│   │   └── security.py
│   └── presentation/
│       ├── app.py
│       ├── dependencies.py
│       ├── errors.py
│       ├── middleware.py
│       ├── schemas.py
│       └── routes/{auth,devices,health}.py
└── tests/
    ├── conftest.py
    ├── test_architecture.py
    ├── test_auth.py
    ├── test_cli.py
    ├── test_config.py
    ├── test_devices.py
    ├── test_health.py
    ├── test_http_contract.py
    ├── test_migrations.py
    ├── test_postgres.py
    └── test_security.py
```

Python package initializer files are omitted from this tree for readability. Setup commands and every environment variable are documented in [README.md](README.md).

## 3. Architecture

Presentation validates HTTP input and maps results/errors. Application services orchestrate authentication and device operations through typed ports. Domain entities and rules have no FastAPI, SQLAlchemy, Pydantic, HTTP, or infrastructure imports. Infrastructure implements persistence, hashing, tokens, configuration, and logging. The factory is the composition boundary.

SQLAlchemy records map explicitly to domain dataclasses. Each request uses its own session; write use cases commit explicitly, while session cleanup rolls back any remaining transaction. The application lifespan disposes the engine. Schema creation belongs to Alembic, never application startup.

An architecture test checks the inward dependency rule. Future Collectors, Rules, Events, Processing, Transactions, and Reporting features can add focused modules within these same layers. See [architecture.md](docs/architecture.md) for flows and rationale.

## 4. Important decisions

| Decision | Reason |
| --- | --- |
| Separate domain and ORM types | Keeps business rules independent of storage and HTTP frameworks. |
| Async PostgreSQL sessions | Fits asynchronous request handling without sharing mutable sessions across requests. |
| Request-scoped unit of work | Makes transaction boundaries and rollback behavior explicit. |
| PostgreSQL row lock during PATCH | Preserves disjoint changes when concurrent requests update the same device. |
| Database-enforced unique external ID | Resolves duplicate registration races consistently as conflicts. |
| Operator-only administrator creation | Avoids default credentials and an unauthenticated bootstrap surface. |
| Short-lived administrator JWT plus active-user lookup | Keeps authentication small and makes account disabling effective for subsequent requests. |
| Immutable external device ID and platform | Separates stable identity from editable management metadata. |
| `PENDING` default, status-based disabling | Supports management without claiming enrollment or device authentication exists. |
| Bounded offset pagination | Provides a simple initial contract without speculative search infrastructure. |
| SQLite isolated tests plus PostgreSQL integration | Combines fast application tests with real engine-specific verification. |

All active administrators share the administration boundary and can manage every device. This is explicitly not a multi-tenant authorization model. RBAC can later be added around the existing authenticated application boundary.

## 5. Database schema and migrations

Initial revision: **`0001`**, in `migrations/versions/0001_users_and_devices.py`.

| Table | Columns |
| --- | --- |
| `users` | UUID primary key `id`; unique `email` varchar(254); `password_hash` varchar(512); `is_active` boolean default true; `created_at` timestamp with time zone. |
| `devices` | UUID primary key `id`; unique `external_device_id` varchar(128); `name` varchar(120); `platform` varchar(32); nullable `app_version` varchar(64); constrained `status` varchar(8) default PENDING; `created_at` and `updated_at` timestamps with time zone; nullable `last_seen_at` timestamp with time zone. |

Constraints include `pk_users`, `uq_users_email`, `pk_devices`, `uq_devices_external_device_id`, and `ck_devices_device_status`. The device creation timestamp has an index. Status values are `ACTIVE`, `DISABLED`, and `PENDING`. There are no device-owner, phone-number, collector, event, transaction, or processing tables.

The Alembic upgrade creates both tables; the downgrade removes them and their data. Do not use `downgrade base` on a populated environment without a reviewed recovery plan. Migration upgrade, drift check, downgrade, re-upgrade, and drift re-check passed on a disposable PostgreSQL 16.15 instance. SQLite migration round trips and offline PostgreSQL SQL generation also passed.

## 6. API endpoints

| Method | Endpoint | Access | Success |
| --- | --- | --- | --- |
| GET | `/health` | Public | 200, process alive |
| GET | `/ready` | Public | 200, database connected; 503 when unavailable |
| POST | `/api/v1/auth/login` | JSON email/password | 200, access token/type/expiry |
| GET | `/api/v1/auth/me` | Active administrator bearer token | 200, safe user profile |
| POST | `/api/v1/devices` | Active administrator bearer token | 201, device and Location header |
| GET | `/api/v1/devices` | Active administrator bearer token | 200, items/limit/offset |
| GET | `/api/v1/devices/{id}` | Active administrator bearer token | 200, device |
| PATCH | `/api/v1/devices/{id}` | Active administrator bearer token | 200, updated device |

PATCH accepts `name`, `app_version`, and `status` only. Explicit null clears `app_version`; omitted fields remain unchanged. Device IDs, platform, external ID, and timestamps are server-owned or immutable through this endpoint. Disabled devices remain manageable by administrators. `last_seen_at` stays null because there is no device contact protocol yet.

Errors use `{ "error": { "code", "message", "request_id" } }`; readiness uses its own status/dependencies object. Responses include `X-Request-ID`, `Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`. Development OpenAPI/docs are available; production disables them.

## 7. Test results

Verification date: **2026-08-31**. Runtime: **Windows, CPython 3.12.13, PostgreSQL 16.15**. PostgreSQL was downloaded as a portable runtime, bound to loopback on a temporary port, protected by a generated password, and stopped after verification. No production database was used.

Final integrated command, with the disposable `TEST_POSTGRES_URL` supplied through the environment:

```text
python -m pytest -q -o faulthandler_timeout=30 -o cache_dir=<scratch-cache> --basetemp=<scratch-test-directory>
116 passed in 39.53s
```

All 116 cases passed, with **zero failures, skips, or warnings** in the final run. Coverage includes liveness/readiness failures and timeouts; correct and incorrect login; protected endpoint rejection; user disabling; required JWT claims, expiry, issuer/audience, signatures and algorithms; secret-safe logs/errors; device creation/retrieval/list/update; duplicates, rollback behavior, SQL metacharacters, input bounds, pagination; host/CORS checks; request correlation; CLI account operations; domain dependency boundaries; and migrations.

The four PostgreSQL integration cases cover timestamp round trips and uniqueness, concurrent duplicate insert behavior, concurrent disjoint PATCH behavior, and status rejection by the database constraint. Without `TEST_POSTGRES_URL`, those four cases intentionally skip; the default suite does not fall back to `DATABASE_URL` for PostgreSQL testing.

A separate real HTTP smoke run launched Uvicorn against the migrated PostgreSQL database and passed 10 checks: health, readiness, missing-auth rejection, login, current user, device creation, duplicate conflict, retrieval, disabling, and listing. The Uvicorn process and database server were both stopped afterward.

During verification, migration metadata was corrected to use an explicit status check constraint, CORS preflight errors were made consistent, and two test assumptions exposed by the PostgreSQL environment were corrected. The final run above occurred after those corrections.

## 8. Code quality and supporting checks

| Check | Observed result |
| --- | --- |
| `python -m ruff check .` | Passed: all checks passed. |
| `python -m black --check .` | Passed: 42 Python files would be left unchanged. |
| `python -m mypy src` | Passed: no issues in 28 source files; strict mode. |
| `python -m compileall -q src tests migrations` | Passed. |
| `python -m pip check` | Passed: no broken requirements. |
| `python -m build --wheel --no-isolation` | Built `relay_backend-0.1.0-py3-none-any.whl`. |
| Compose and GitHub Actions YAML parsing | Passed using PyYAML 6.0.3; parsing is not Docker/Actions execution. |
| Source whitespace comparison | Checked with Git's no-index diff against an empty baseline; no whitespace errors in the final source export. |

Primary pinned versions include FastAPI 0.141.1, Pydantic 2.13.5, SQLAlchemy 2.0.52, Alembic 1.19.1, PyJWT 2.13.0, Argon2-CFFI 25.1.0, Pytest 9.1.1, Ruff 0.16.5, Black 26.5.1, and MyPy 1.20.2. The lockfiles record the complete selected runtime/development dependencies. Additional local build/YAML verification used setuptools 84.0.0, wheel 0.48.0, and PyYAML 6.0.3; these are not runtime requirements.

Machine-readable migration, test, and HTTP evidence is in [verification-evidence.json](docs/verification-evidence.json).

## 9. Security notes

- No usable service secret or default administrator password is supplied. `.env.example` leaves required secrets empty. `.env` and generated credentials are excluded from the delivery.
- Passwords use Argon2id with explicit costs. Unknown-user authentication performs a dummy hash verification. Hashing work runs off the event loop with bounded concurrent hash work.
- JWT validation fixes the allowed algorithm and requires subject, issuer, audience, issued-at, not-before, expiry, token ID, and token type. Administrator tokens are not device credentials.
- CORS denies browser origins by default. Production forbids wildcard origins, non-HTTPS browser origins, debug logging, and development-only allowed hosts.
- Request bodies are bounded to 16 KiB. Device strings, enums, UUIDs, and pagination inputs are validated. ORM queries use bound values.
- API errors exclude stack traces and rejected payloads. Logs use allowlisted event messages and metadata; arbitrary third-party log text is suppressed. Proxy/hosting logs require separate controls.
- Local Compose exposes ports only on loopback. The backend image uses a non-root user; Compose drops capabilities and uses a read-only backend filesystem.

These controls do not constitute a penetration test or a formal security certification. TLS, credential-abuse throttling, operational monitoring, database privileges/backups, and secret rotation must be configured before public exposure.

## 10. Known limitations and unverified areas

1. **Docker image build and Compose startup were not executed:** Docker is not available on this machine. Their files were reviewed and YAML parsed; the backend itself was built and run locally against PostgreSQL.
2. **GitHub-hosted CI was not executed:** the workflow is delivered but nothing was pushed or deployed. Equivalent local quality, migration, and test checks passed on Windows; Linux container execution remains a separate check.
3. No RBAC, tenancy, MFA, refresh tokens, per-token revocation, password reset, or device credentials. Disabling an account rejects its subsequent authenticated requests; it does not cancel a request already in progress.
4. No built-in login rate limiter or production reverse proxy. Do not expose the local stack publicly until those deployment controls are in place.
5. No formal vulnerability scan, load benchmark, production recovery exercise, or long-running soak test was performed.
6. Readiness checks database connectivity, not migration revision or every future dependency. Operators must apply migrations explicitly.
7. The dependency locks are version pins, not artifact-hash locks. Container base images remain maintenance inputs. Other Python versions and operating systems were not run locally.
8. CLI logic and database effects were tested with simulated hidden input; no manual terminal UX certification was performed.

## 11. Exact proposed Sprint 2 scope — not implemented

1. Android application foundation with enrollment/status UI, safe networking, secure local storage, and lifecycle-aware synchronization.
2. Operator-issued short-lived, one-time enrollment challenges with expiry, replay prevention, attempt limits, and atomic consumption.
3. Per-installation device authentication separate from administrator JWTs, including revocation and renewal/rotation semantics.
4. Explicit device lifecycle: enrollment, activation, disable/revoke, duplicate identity handling, and re-enrollment.
5. Versioned per-device remote configuration pull, validation, acknowledgement, unchanged responses, and safe offline fallback.
6. `last_seen_at` updates only after authenticated contact, plus administration visibility into contact/configuration state.
7. Two-device end-to-end tests covering isolation, enrollment races/replays, disabled credentials, configuration versions, offline recovery, and secret-safe logs.

No personal-content collection, SMS, notifications, images/files ingestion, OCR, AI, payment logic, dashboard, generic remote command execution, or production deployment belongs in that proposed sprint. See [the detailed Sprint 2 proposal](docs/sprint-2-proposal.md) for acceptance criteria and decisions to settle before implementation.
