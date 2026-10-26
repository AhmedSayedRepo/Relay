import secrets

import pytest
from pydantic import ValidationError

from relay.infrastructure.config import Settings, load_settings


def values(**overrides):
    return dict(
        _env_file=None,
        **{
            "app_env": "development",
            "database_url": "postgresql+asyncpg://relay:unit-test-only@localhost/relay",
            "jwt_secret": secrets.token_urlsafe(48),
            **overrides,
        },
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"jwt_secret": "short"},
        {"jwt_secret": "a" * 64},
        {"jwt_algorithm": "none"},
        {"jwt_algorithm": "HS512"},
        {"access_token_expire_minutes": 0},
        {"access_token_expire_minutes": 61},
        {"database_url": "sqlite+aiosqlite:///dev.db"},
        {"database_url": "postgresql+asyncpg://localhost/relay"},
        {"cors_origins": ["*"]},
        {"cors_origins": ["https://a.example/path"]},
        {"cors_origins": ["https://a.example?secret=value"]},
        {"cors_origins": ["https://a.example:invalid"]},
        {"allowed_hosts": ["*"]},
        {"allowed_hosts": []},
        {"ocr_languages": []},
        {"ocr_languages": ["eng", "eng"]},
        {"ocr_languages": ["EN"]},
        {"ocr_languages": ["ENG"]},
        {"ocr_timeout_seconds": 0},
        {"ocr_timeout_seconds": 61},
        {"processing_batch_limit": 101},
    ],
)
def test_invalid_configuration_fails(overrides):
    with pytest.raises(ValidationError):
        Settings(**values(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"log_level": "DEBUG"},
        {"allowed_hosts": ["testserver"]},
        {"cors_origins": ["http://admin.example.com"]},
        {"database_url": "sqlite+aiosqlite:///test.db"},
    ],
)
def test_production_rejects_unsafe_configuration(overrides):
    data = values(app_env="production", allowed_hosts=["api.example.com"])
    data.update(overrides)
    with pytest.raises(ValidationError):
        Settings(**data)


def test_production_accepts_explicit_secure_configuration():
    settings = Settings(
        **values(
            app_env="production",
            allowed_hosts=["api.example.com"],
            cors_origins=["https://admin.example.com"],
        )
    )
    assert settings.app_env == "production"
    assert settings.jwt_secret.get_secret_value() not in repr(settings)
    assert "unit-test-only" not in repr(settings)


def test_environment_loading(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("CORS_ORIGINS", '["https://admin.example.com"]')
    assert load_settings().cors_origins == ["https://admin.example.com"]


def test_settings_debug_dump_rejected(monkeypatch):
    monkeypatch.setenv("PYDANTIC_SETTINGS_DEBUG", "1")
    with pytest.raises(RuntimeError, match="must be disabled"):
        load_settings()


def test_invalid_configuration_does_not_echo_secrets(monkeypatch):
    sentinel = "private-database-password"
    monkeypatch.setenv("DATABASE_URL", sentinel)
    monkeypatch.setenv("JWT_SECRET", "private-signing-secret")
    with pytest.raises(RuntimeError) as error:
        load_settings()
    assert sentinel not in str(error.value)
    assert "private-signing-secret" not in str(error.value)
