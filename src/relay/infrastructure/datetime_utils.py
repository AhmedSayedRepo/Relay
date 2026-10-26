from datetime import UTC, datetime


def aware(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamps while preserving PostgreSQL values."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
