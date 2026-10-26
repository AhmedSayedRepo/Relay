from typing import Literal

from pydantic import BaseModel, Field, RootModel

from relay.domain.agent import JsonValue
from relay.presentation.schemas import InputModel


class OperationData(RootModel[dict[str, JsonValue]]):
    pass


class OperationPage(BaseModel):
    items: list[dict[str, JsonValue]]
    total: int
    limit: int
    offset: int


class DuplicateResolutionRequest(InputModel):
    decision: Literal["CONFIRMED_DUPLICATE", "NOT_DUPLICATE", "UNRESOLVED"]
    note: str | None = Field(default=None, min_length=1, max_length=500)


class RetryResponse(BaseModel):
    id: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: str
