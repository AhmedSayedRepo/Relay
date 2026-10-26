"""Bounded operator-facing read and action models."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DateWindow:
    start: datetime
    end_exclusive: datetime


@dataclass(frozen=True, slots=True)
class TransactionQuery:
    window: DateWindow
    search: str | None = None
    review_status: str | None = None
    duplicate_status: str | None = None
    receipt_type: str | None = None
    currency: str | None = None
    device_id: UUID | None = None
    source_type: str | None = None
    processing_status: str | None = None
    sort_by: str = "created_at"
    sort_direction: str = "desc"
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class DuplicateGroupQuery:
    window: DateWindow
    search: str | None = None
    device_id: UUID | None = None
    currency: str | None = None
    receipt_type: str | None = None
    sort_by: str = "created_at"
    sort_direction: str = "desc"
    limit: int = 50
    offset: int = 0
