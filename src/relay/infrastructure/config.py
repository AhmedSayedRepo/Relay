from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

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
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    readiness_timeout_seconds: float = Field(default=3, gt=0, le=30)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: float = Field(default=5, gt=0, le=30)

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
        resolved_storage = self.object_storage_root.expanduser().resolve()
        if resolved_storage == Path(resolved_storage.anchor):
            raise ValueError("OBJECT_STORAGE_ROOT cannot be a filesystem root.")
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
