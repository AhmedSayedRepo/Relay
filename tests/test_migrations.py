import os
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_migration_upgrade_check_downgrade_roundtrip(tmp_path):
    database = tmp_path / "migration.sqlite"
    env = dict(
        os.environ,
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{database}",
        JWT_SECRET=secrets.token_urlsafe(48),
        PYDANTIC_SETTINGS_DEBUG="0",
    )
    root = Path(__file__).parents[1]

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("upgrade", "head")
    alembic("check")
    with sqlite3.connect(database) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"users", "devices", "alembic_version"} <= tables
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0010",
        )
        assert "receipt_reviewed_fields" in tables
        assert {"training_examples", "training_capture_consents"} <= tables
        assert {"sms_verification_links", "receipt_sms_verifications"} <= tables
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(receipt_candidates)")
        }
        assert "resolution_result" in columns
    alembic("downgrade", "base")
    with sqlite3.connect(database) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "users" not in tables and "devices" not in tables
    alembic("upgrade", "head")
    alembic("check")


def test_sprint_1_database_upgrades_in_place_to_sprint_3(tmp_path):
    database = tmp_path / "upgrade.sqlite"
    env = dict(
        os.environ,
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{database}",
        JWT_SECRET=secrets.token_urlsafe(48),
        PYDANTIC_SETTINGS_DEBUG="0",
    )
    root = Path(__file__).parents[1]

    def alembic(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("0001")
    with sqlite3.connect(database) as conn:
        before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "devices" in before and "device_installations" not in before
    alembic("head")
    with sqlite3.connect(database) as conn:
        after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {
            "enrollment_challenges",
            "device_installations",
            "device_credentials",
            "device_configurations",
            "configuration_deliveries",
            "raw_events",
        } <= after
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0010",
        )


def test_sprint_2_database_upgrades_without_modifying_prior_schema(tmp_path):
    database = tmp_path / "sprint2-upgrade.sqlite"
    env = dict(
        os.environ,
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{database}",
        JWT_SECRET=secrets.token_urlsafe(48),
        PYDANTIC_SETTINGS_DEBUG="0",
    )
    root = Path(__file__).parents[1]

    def upgrade(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    upgrade("0002")
    with sqlite3.connect(database) as conn:
        before = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert "device_installations" in before and "raw_events" not in before
    upgrade("head")
    with sqlite3.connect(database) as conn:
        after = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert "device_installations" in after and "raw_events" in after
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0010",
        )


def test_offline_postgresql_migration_compiles():
    root = Path(__file__).parents[1]
    env = dict(
        os.environ,
        APP_ENV="test",
        JWT_SECRET=secrets.token_urlsafe(48),
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1/relay_test",
        PYDANTIC_SETTINGS_DEBUG="0",
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE users" in result.stdout
    assert "CREATE TABLE devices" in result.stdout
    assert "TIMESTAMP WITH TIME ZONE" in result.stdout
    assert "ck_devices_device_status" in result.stdout
    assert "CREATE TABLE raw_events" in result.stdout
    assert "CREATE TABLE binary_objects" in result.stdout
    assert "CREATE TABLE binary_upload_sessions" in result.stdout
    assert "CREATE TABLE processing_jobs" in result.stdout
    assert "CREATE TABLE processing_attempts" in result.stdout
    assert "CREATE TABLE ocr_results" in result.stdout
    assert "CREATE TABLE receipt_candidates" in result.stdout
    assert "CREATE TABLE receipt_review_audit" in result.stdout
    assert "CREATE TABLE receipt_reviewed_fields" in result.stdout
    assert "resolution_result" in result.stdout
    assert "CREATE TABLE operator_audit" in result.stdout
    assert "JSONB" in result.stdout
