from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    ADMIN = "ADMIN"  # everything, including users and devices
    REVIEWER = "REVIEWER"  # review receipts; read everything operators see
    VIEWER = "VIEWER"  # read-only: dashboard, transactions, reports


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    email: str
    password_hash: str = field(repr=False)
    is_active: bool
    created_at: datetime
    role: Role = Role.ADMIN
