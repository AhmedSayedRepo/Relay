from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = Field(default="Relay", min_length=1, max_length=80)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr
    jwt_secret: SecretStr
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = Field(default="relay", min_length=1)
    jwt_audience: str = Field(default="relay-admin", min_length=1)
    access_token_expire_minutes: int = Field(default=15, ge=1, le=60)
    # Signed-in portal session (httpOnly cookie): renewed while in use, ends
    # after this long without any activity.
    session_idle_hours: int = Field(default=12, ge=1, le=168)
    device_access_expire_minutes: int = Field(default=15, ge=1, le=60)
    device_refresh_expire_days: int = Field(default=30, ge=1, le=90)
    enrollment_expire_minutes: int = Field(default=10, ge=1, le=10)
    device_contact_coalesce_seconds: int = Field(default=60, ge=1, le=300)
    raw_event_retention_days: int = Field(default=30, ge=1, le=365)
    image_object_retention_days: int = Field(default=14, ge=1, le=90)
    image_upload_session_hours: int = Field(default=24, ge=1, le=72)
    image_max_active_uploads: int = Field(default=3, ge=1, le=10)
    image_uploads_per_hour: int = Field(default=30, ge=1, le=100)
    image_stored_bytes_per_device: int = Field(
        default=64 * 1024 * 1024, ge=8 * 1024 * 1024, le=1024 * 1024 * 1024
    )
    object_storage_root: Path = Path("./relay-object-storage")
    training_dataset_root: Path = Path("./relay-training-dataset")
    # Receipts show local wall-clock time; bank SMS times are absolute.
    receipt_timezone: str = Field(default="Africa/Cairo", min_length=1, max_length=64)
    sms_match_before_minutes: int = Field(default=2, ge=0, le=30)
    sms_match_after_minutes: int = Field(default=10, ge=1, le=60)
    ocr_tesseract_executable: Path = Path("tesseract")
    ocr_tessdata_dir: Path | None = None
    ocr_languages: list[str] = Field(default_factory=lambda: ["eng", "ara"])
    ocr_timeout_seconds: float = Field(default=20, ge=1, le=60)
    processing_stale_minutes: int = Field(default=5, ge=1, le=60)
    processing_batch_limit: int = Field(default=10, ge=1, le=100)
    # Explicit rollout gate. This task prepares the path, not live enablement.
    receipt_evidence_enabled: bool = False
    # Reserved for a later explicitly approved rollout; Sprint 8 rejects true.
    extraction_auto_trust_enabled: bool = False
    exception_review_enabled: bool = True
    operator_audit_retention_days: int = Field(default=365, ge=30, le=2555)
    operator_frontend_dir: Path = Path("./frontend/dist")
    cors_origins: list[str] = Field(default_factory=list)
    # Account recovery by email is optional; recovery codes always work.
    operator_public_url: str | None = Field(default=None, max_length=200)
    password_reset_minutes: int = Field(default=15, ge=5, le=60)
    smtp_host: str | None = Field(default=None, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_username: str | None = Field(default=None, max_length=254)
    smtp_password: SecretStr | None = None
    smtp_from: str | None = Field(default=None, max_length=254)
    # Daily summary email at 23:59 (receipt_timezone); off when unset.
    daily_report_email: str | None = Field(default=None, max_length=254)
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    readiness_timeout_seconds: float = Field(default=3, gt=0, le=30)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: float = Field(default=5, gt=0, le=30)

    @field_validator(
        "operator_public_url",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "smtp_from",
        "daily_report_email",
        mode="before",
    )
    @classmethod
    def blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("jwt_secret")
    @classmethod
    def secure_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if (
            len(raw.encode()) < 32
            or len(set(raw)) < 12
            or any(
                marker in raw.lower()
                for marker in ("change-me", "changeme", "replace-me", "your-secret")
            )
        ):
            raise ValueError(
                "JWT_SECRET must be a randomly generated secret (32+ bytes)."
            )
        return value

    @field_validator("cors_origins")
    @classmethod
    def valid_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
                or "*" in origin
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins.")
            try:
                _ = parsed.port
            except ValueError:
                raise ValueError("Invalid CORS origin port.") from None
        return origins

    @field_validator("allowed_hosts")
    @classmethod
    def valid_hosts(cls, hosts: list[str]) -> list[str]:
        if not hosts or any(
            not host or any(c in host for c in "*/:@?# ") for host in hosts
        ):
            raise ValueError(
                "ALLOWED_HOSTS must contain explicit hostnames without ports."
            )
        return hosts

    @field_validator("ocr_languages")
    @classmethod
    def valid_ocr_languages(cls, languages: list[str]) -> list[str]:
        if (
            not 1 <= len(languages) <= 4
            or len(set(languages)) != len(languages)
            or any(
                len(language) != 3
                or not language.isascii()
                or not language.isalpha()
                or not language.islower()
                for language in languages
            )
        ):
            raise ValueError("OCR_LANGUAGES must contain 1 to 4 unique ISO codes.")
        return languages

    @field_validator("receipt_timezone")
    @classmethod
    def valid_receipt_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("RECEIPT_TIMEZONE must be an IANA time zone.") from None
        return value

    @property
    def email_reset_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.operator_public_url)

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        if self.extraction_auto_trust_enabled:
            raise ValueError("Automatic extraction trust is not available in Sprint 8.")
        if not self.exception_review_enabled:
            raise ValueError("Exception review must remain enabled in Sprint 8.")
        try:
            url = make_url(self.database_url.get_secret_value())
        except ArgumentError:
            raise ValueError("DATABASE_URL is invalid.") from None
        sqlite_test = self.app_env == "test" and url.drivername == "sqlite+aiosqlite"
        if not sqlite_test and url.drivername != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use postgresql+asyncpg.")
        if not sqlite_test and not all(
            (url.host, url.database, url.username, url.password)
        ):
            raise ValueError(
                "DATABASE_URL requires host, database, user, and password."
            )
        if self.app_env == "production":
            if self.log_level == "DEBUG":
                raise ValueError("DEBUG logging is not permitted in production.")
            if any(urlsplit(origin).scheme != "https" for origin in self.cors_origins):
                raise ValueError("Production CORS origins must use HTTPS.")
            if any(
                host in {"localhost", "127.0.0.1", "testserver"}
                for host in self.allowed_hosts
            ):
                raise ValueError(
                    "Production ALLOWED_HOSTS must name the actual service."
                )
        if self.operator_public_url is not None:
            public = urlsplit(self.operator_public_url)
            local = public.hostname in {"localhost", "127.0.0.1"}
            if public.scheme not in {"https", "http"} or not public.hostname:
                raise ValueError("OPERATOR_PUBLIC_URL must be an absolute URL.")
            if public.scheme != "https" and not (
                local and self.app_env != "production"
            ):
                raise ValueError("OPERATOR_PUBLIC_URL must use HTTPS.")
            if public.query or public.fragment:
                raise ValueError("OPERATOR_PUBLIC_URL cannot have a query.")
        if bool(self.smtp_host) != bool(self.smtp_from):
            raise ValueError("SMTP_HOST and SMTP_FROM must be set together.")
        resolved_storage = self.object_storage_root.expanduser().resolve()
        if resolved_storage == Path(resolved_storage.anchor):
            raise ValueError("OBJECT_STORAGE_ROOT cannot be a filesystem root.")
        resolved_dataset = self.training_dataset_root.expanduser().resolve()
        if resolved_dataset == Path(resolved_dataset.anchor):
            raise ValueError("TRAINING_DATASET_ROOT cannot be a filesystem root.")
        if resolved_dataset == resolved_storage:
            raise ValueError("TRAINING_DATASET_ROOT must differ from object storage.")
        frontend = self.operator_frontend_dir.expanduser().resolve()
        if frontend == Path(frontend.anchor):
            raise ValueError("OPERATOR_FRONTEND_DIR cannot be a filesystem root.")
        return self


def load_settings() -> Settings:
    # Keep settings source debug dumps disabled: they may contain raw secrets.
    import os

    if os.environ.get("PYDANTIC_SETTINGS_DEBUG", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("PYDANTIC_SETTINGS_DEBUG must be disabled.")
    try:
        return Settings()
    except ValueError:
        # A generic exception prevents driver/settings parser error values leaking.
        raise RuntimeError(
            "Invalid Relay configuration; check environment settings."
        ) from None
