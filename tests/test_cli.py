import asyncio
import secrets
import sqlite3
import sys

import pytest
from argon2 import PasswordHasher

from relay.infrastructure import cli
from relay.infrastructure.config import load_settings
from relay.infrastructure.database import Base, Database


def test_operator_can_create_and_disable_admin_without_plaintext_storage(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "operator.sqlite"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))

    async def initialize():
        database = Database(load_settings())
        try:
            async with database.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await database.close()

    asyncio.run(initialize())
    password = secrets.token_urlsafe(24)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: password)
    monkeypatch.setattr(
        sys, "argv", ["relay", "create-admin", "--email", "Operator@example.com"]
    )
    assert cli.main() == 0
    with sqlite3.connect(path) as connection:
        email, stored_hash, active = connection.execute(
            "SELECT email,password_hash,is_active FROM users"
        ).fetchone()
        assert email == "operator@example.com"
        assert stored_hash != password
        assert PasswordHasher().verify(stored_hash, password)
        assert active == 1
    assert cli.main() == 1  # Duplicate email cannot overwrite an existing account.
    monkeypatch.setattr(
        sys, "argv", ["relay", "disable-user", "--email", "operator@example.com"]
    )
    assert cli.main() == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT is_active FROM users").fetchone() == (0,)
    captured = capsys.readouterr()
    assert password not in captured.out + captured.err
    assert stored_hash not in captured.out + captured.err


def test_operator_password_prompt_requires_terminal(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        sys, "argv", ["relay", "create-admin", "--email", "admin@example.com"]
    )
    assert cli.main() == 1
    assert "Traceback" not in capsys.readouterr().err


def test_operator_password_confirmation_failure_is_safe(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    passwords = iter(["private-first-password", "private-second-password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(passwords))
    monkeypatch.setattr(
        sys, "argv", ["relay", "create-admin", "--email", "admin@example.com"]
    )
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert "private-" not in captured.out + captured.err


def test_retention_purge_command_needs_no_email_or_password(monkeypatch, capsys):
    calls = []

    async def fake_run(command, email, password):
        calls.append((command, email, password))

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["relay", "purge-expired-events"])
    assert cli.main() == 0
    assert calls == [("purge-expired-events", None, None)]
    assert capsys.readouterr().out == "Operation completed.\n"

    monkeypatch.setattr(
        sys,
        "argv",
        ["relay", "purge-expired-events", "--email", "admin@example.com"],
    )
    assert cli.main() == 1


def test_processing_command_is_bounded_and_needs_no_email(monkeypatch, capsys):
    calls = []

    async def fake_run(command, email, password, limit=None):
        calls.append((command, email, password, limit))

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(
        sys, "argv", ["relay", "process-pending-images", "--limit", "7"]
    )
    assert cli.main() == 0
    assert calls == [("process-pending-images", None, None, 7)]
    assert capsys.readouterr().out == "Operation completed.\n"

    monkeypatch.setattr(
        sys, "argv", ["relay", "process-pending-images", "--limit", "101"]
    )
    with pytest.raises(SystemExit):
        cli.main()


def test_operator_can_reset_password_list_admins_and_issue_codes(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "operator.sqlite"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))

    async def initialize():
        database = Database(load_settings())
        try:
            async with database.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await database.close()

    asyncio.run(initialize())
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    first, second = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: first)
    monkeypatch.setattr(
        sys, "argv", ["relay", "create-admin", "--email", "admin@example.com"]
    )
    assert cli.main() == 0
    created = capsys.readouterr().out
    assert created.count("-") >= 20  # ten XXXX-XXXX-XXXX recovery codes
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: second)
    monkeypatch.setattr(
        sys, "argv", ["relay", "reset-password", "--email", "admin@example.com"]
    )
    assert cli.main() == 0
    with sqlite3.connect(path) as connection:
        (stored_hash,) = connection.execute(
            "SELECT password_hash FROM users"
        ).fetchone()
    assert PasswordHasher().verify(stored_hash, second)
    monkeypatch.setattr(sys, "argv", ["relay", "list-admins"])
    assert cli.main() == 0
    assert "admin@example.com" in capsys.readouterr().out
    monkeypatch.setattr(
        sys, "argv", ["relay", "recovery-codes", "--email", "admin@example.com"]
    )
    assert cli.main() == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM user_recovery_codes"
        ).fetchone() == (10,)
    monkeypatch.setattr(
        sys, "argv", ["relay", "reset-password", "--email", "nobody@example.com"]
    )
    assert cli.main() == 1
    output = capsys.readouterr()
    assert second not in output.out + output.err


def test_reopen_rejected_accepts_an_optional_device(monkeypatch):
    calls = []

    async def fake_run(command, email, password, limit=None, **kwargs):
        calls.append((command, kwargs.get("device_id")))

    monkeypatch.setattr(cli, "run", fake_run)
    device = "72d01536-ba2b-4bd0-8139-586270618b24"
    monkeypatch.setattr(sys, "argv", ["relay", "reopen-rejected", "--device", device])
    assert cli.main() == 0
    monkeypatch.setattr(sys, "argv", ["relay", "reopen-rejected"])
    assert cli.main() == 0
    assert [str(value) if value else None for _, value in calls] == [device, None]
