from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    email: str
    password_hash: str = field(repr=False)
    is_active: bool
    created_at: datetime
