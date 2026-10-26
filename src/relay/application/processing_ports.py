"""Ports used by the processing application service."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from relay.domain.agent import JsonValue
from relay.domain.binary_objects import BinaryObject
from relay.domain.events import RawEvent
from relay.domain.processing import (
    OcrDocument,
    ParsedReceipt,
    PreprocessedImage,
    ProcessingFailure,
    ProcessingJob,
    ReceiptCandidate,
)


class OcrProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    @property
    def available_languages(self) -> tuple[str, ...]: ...

    async def extract(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument: ...


class ImagePreprocessor(Protocol):
    def preprocess(self, data: bytes) -> PreprocessedImage: ...


class ReceiptParser(Protocol):
    @property
    def parser_type(self) -> str: ...

    @property
    def parser_version(self) -> str: ...

    def parse(self, document: OcrDocument) -> ParsedReceipt: ...


class ProcessingRepository(Protocol):
    async def nearby_sms(
        self, device_id: UUID, timestamp: datetime
    ) -> list[RawEvent]: ...
    async def exception_command(
        self, command_id: UUID
    ) -> dict[str, JsonValue] | None: ...
    async def save_exception_command(
        self,
        command_id: UUID,
        candidate: ReceiptCandidate,
        reviewer_id: UUID,
        body_digest: str,
        response: dict[str, JsonValue],
        now: datetime,
    ) -> None: ...
    async def source_for_object(
        self, object_id: UUID
    ) -> tuple[UUID, UUID, datetime] | None: ...

    async def ensure_job(
        self,
        *,
        object_id: UUID,
        raw_event_id: UUID,
        device_id: UUID,
        processor_version: str,
        parser_type: str,
        parser_version: str,
        ocr_provider: str,
        requested_languages: tuple[str, ...],
        expires_at: datetime,
        now: datetime,
    ) -> ProcessingJob: ...

    async def claim_next(
        self, now: datetime, stale_before: datetime
    ) -> tuple[ProcessingJob, UUID] | None: ...

    async def job(self, job_id: UUID) -> ProcessingJob | None: ...
    async def list_jobs(
        self,
        *,
        status: str | None,
        object_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[ProcessingJob]: ...
    async def attempts(self, job_id: UUID) -> list[dict[str, JsonValue]]: ...
    async def owns_attempt(self, job: ProcessingJob, attempt_id: UUID) -> bool: ...
    async def save_success(
        self,
        job: ProcessingJob,
        attempt_id: UUID,
        document: OcrDocument,
        parsed: ParsedReceipt,
        duplicate_of: UUID | None,
        now: datetime,
    ) -> ReceiptCandidate | None: ...
    async def save_failure(
        self,
        job: ProcessingJob,
        attempt_id: UUID,
        failure: ProcessingFailure,
        *,
        transient: bool,
        now: datetime,
    ) -> bool: ...
    async def canonical_reference(
        self,
        device_id: UUID,
        receipt_type: str,
        normalized_reference: str,
        *,
        exclude_candidate_id: UUID | None = None,
    ) -> UUID | None: ...
    async def untrusted_reference_collision(
        self,
        device_id: UUID,
        receipt_type: str,
        normalized_reference: str,
        *,
        exclude_candidate_id: UUID,
    ) -> bool: ...
    async def save_reviewed_field(
        self,
        candidate: ReceiptCandidate,
        reviewer_id: UUID,
        command_id: UUID,
        field: str,
        action: str,
        value: str | None,
        reason: str | None,
        candidates: list[JsonValue],
        provenance: dict[str, JsonValue],
        now: datetime,
    ) -> None: ...
    async def reviewed_fields_for_candidate(
        self, candidate_id: UUID
    ) -> list[dict[str, JsonValue]]: ...
    async def review_metrics(self) -> dict[str, JsonValue]: ...
    async def lock_device(self, device_id: UUID) -> None: ...
    async def candidate(
        self, candidate_id: UUID, *, for_update: bool = False
    ) -> ReceiptCandidate | None: ...
    async def list_candidates(
        self,
        *,
        review_status: str | None,
        receipt_type: str | None,
        device_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[ReceiptCandidate]: ...
    async def next_exception(self) -> ReceiptCandidate | None: ...
    async def ocr_for_candidate(
        self, candidate_id: UUID
    ) -> dict[str, JsonValue] | None: ...
    async def audit_for_candidate(
        self, candidate_id: UUID
    ) -> list[dict[str, JsonValue]]: ...
    async def review(
        self,
        candidate: ReceiptCandidate,
        reviewer_id: UUID,
        action: str,
        before: dict[str, JsonValue],
        after: dict[str, JsonValue],
        now: datetime,
    ) -> None: ...
    async def purge_expired(self, before: datetime) -> int: ...


class BinaryLookup(Protocol):
    async def object(self, object_id: UUID) -> BinaryObject | None: ...


class ProcessingUnitOfWork(Protocol):
    @property
    def processing(self) -> ProcessingRepository: ...

    @property
    def binaries(self) -> BinaryLookup: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class ObjectReader(Protocol):
    def read(self, storage_key: str, maximum: int) -> bytes: ...
