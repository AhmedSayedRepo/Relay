"""Sender email settings entered by an admin in the portal (like QA Studio's
Gmail sender): sender name, Gmail address and app password. The password is
stored encrypted and is never sent back to the browser."""

import base64
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import DateTime, Integer, String, Text, Uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from relay.infrastructure.config import Settings
from relay.infrastructure.database import Base
from relay.infrastructure.mailer import MailConfig

ROW_ID = 1
GMAIL_HOST = "smtp.gmail.com"
GMAIL_SSL_PORT = 465


class EmailSettingsRow(Base):
    __tablename__ = "email_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sender_name: Mapped[str] = mapped_column(String(120), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(254), nullable=False)
    smtp_host: Mapped[str] = mapped_column(String(253), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_security: Mapped[str] = mapped_column(String(16), nullable=False)
    password_encrypted: Mapped[str | None] = mapped_column(Text)
    report_email: Mapped[str | None] = mapped_column(String(254))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[UUID | None] = mapped_column(Uuid)


def _fernet(settings: Settings) -> Fernet:
    secret = settings.jwt_secret.get_secret_value().encode("utf-8")
    key = hashlib.sha256(b"relay-email-settings:" + secret).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(settings: Settings, password: str) -> str:
    return _fernet(settings).encrypt(password.encode("utf-8")).decode("ascii")


def decrypt(settings: Settings, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None  # e.g. JWT_SECRET changed: the admin enters it again


async def load_row(session: AsyncSession) -> EmailSettingsRow | None:
    return await session.get(EmailSettingsRow, ROW_ID)


def env_config(settings: Settings) -> MailConfig | None:
    if not settings.smtp_host or not settings.smtp_from:
        return None
    password = settings.smtp_password
    return MailConfig(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        password=password.get_secret_value() if password else None,
        sender_email=settings.smtp_from,
        sender_name="Relay",
        report_email=settings.daily_report_email,
    )


async def mail_config(session: AsyncSession, settings: Settings) -> MailConfig | None:
    """Portal settings first (when complete), else the server's .env SMTP."""
    row = await load_row(session)
    if row is not None:
        password = decrypt(settings, row.password_encrypted)
        if password:
            return MailConfig(
                host=row.smtp_host,
                port=row.smtp_port,
                security="ssl" if row.smtp_security == "ssl" else "starttls",
                username=row.sender_email,
                password=password,
                sender_email=row.sender_email,
                sender_name=row.sender_name,
                report_email=row.report_email or settings.daily_report_email,
            )
    return env_config(settings)


async def save(
    session: AsyncSession,
    settings: Settings,
    *,
    sender_name: str,
    sender_email: str,
    password: str | None,
    report_email: str | None,
    actor_id: UUID,
) -> EmailSettingsRow:
    row = await load_row(session)
    if row is None:
        row = EmailSettingsRow(
            id=ROW_ID,
            smtp_host=GMAIL_HOST,
            smtp_port=GMAIL_SSL_PORT,
            smtp_security="ssl",
        )
        session.add(row)
    row.sender_name = sender_name
    row.sender_email = sender_email
    row.report_email = report_email
    if password:
        row.password_encrypted = encrypt(settings, password)
    row.updated_at = datetime.now(UTC)
    row.updated_by = actor_id
    await session.commit()
    return row
