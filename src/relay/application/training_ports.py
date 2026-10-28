"""Ports for the reviewed-receipt training dataset."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from relay.domain.agent import JsonValue


@dataclass(frozen=True, slots=True)
class CaptureSource:
    candidate_id: UUID
    device_id: UUID
    transaction_group_id: str
    storage_key: str
    content_type: str
    content_hash: str
    resolution: JsonValue


@dataclass(frozen=True, slots=True)
class TrainingExample:
    id: UUID
    candidate_id: UUID
    device_id: UUID
    transaction_group_id: str
    split: str
    image_key: str
    image_sha256: str
    content_type: str
    labels: dict[str, JsonValue]
    label_version: str
    created_at: datetime


class TrainingRepository(Protocol):
    async def device_exists(self, device_id: UUID) -> bool: ...
    async def consent(self, device_id: UUID) -> bool: ...
    async def set_consent(
        self, device_id: UUID, enabled: bool, actor_id: UUID, now: datetime
    ) -> None: ...
    async def capture_sources(self, *, scan_limit: int) -> list[CaptureSource]: ...
    async def add_example(self, example: TrainingExample) -> None: ...
    async def examples(self) -> list[TrainingExample]: ...
    async def summary(self) -> dict[str, int]: ...
    async def delete_device(self, device_id: UUID) -> tuple[int, list[str]]: ...


class TrainingUnitOfWork(Protocol):
    @property
    def training(self) -> TrainingRepository: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class SourceImageReader(Protocol):
    def read(self, storage_key: str, maximum: int) -> bytes: ...


class DatasetStore(Protocol):
    def put(self, sha256: str, content_type: str, data: bytes) -> str: ...
    def read(self, key: str, maximum: int) -> bytes: ...
    def delete(self, key: str) -> None: ...
