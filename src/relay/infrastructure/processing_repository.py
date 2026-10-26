"""SQLAlchemy persistence and locking for the processing pipeline."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.errors import Conflict
from relay.domain.agent import JsonValue
from relay.domain.events import RawEvent
from relay.domain.processing import (
    AttemptStatus,
    JobStatus,
    OcrDocument,
    ParsedReceipt,
    ProcessingFailure,
    ProcessingJob,
    ReceiptCandidate,
    ReceiptType,
    ReviewStatus,
)
from relay.domain.review_trust import initial_resolution, reviewed_value
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.event_repository import to_event
from relay.infrastructure.models import DeviceRow
from relay.infrastructure.processing_models import (
    OcrResultRow,
    ProcessingAttemptRow,
    ProcessingJobRow,
    ReceiptCandidateRow,
    ReceiptEvidenceRow,
    ReceiptExceptionCommandRow,
    ReceiptReviewAuditRow,
    ReceiptReviewedFieldRow,
)


def to_job(row: ProcessingJobRow) -> ProcessingJob:
    return ProcessingJob(
        row.id,
        row.object_id,
        row.raw_event_id,
        row.device_id,
        row.processor_version,
        row.parser_type,
        row.parser_version,
        row.ocr_provider,
        tuple(row.requested_languages),
        JobStatus(row.status),
        row.attempt_count,
        row.max_attempts,
        aware(row.next_attempt_at),
        aware(row.created_at),
        aware(row.updated_at),
        aware(row.expires_at),
        aware(row.started_at) if row.started_at else None,
        aware(row.completed_at) if row.completed_at else None,
        ProcessingFailure(row.failure_code) if row.failure_code else None,
    )


def to_candidate(row: ReceiptCandidateRow) -> ReceiptCandidate:
    return ReceiptCandidate(
        row.id,
        row.job_id,
        row.object_id,
        row.raw_event_id,
        row.device_id,
        ReceiptType(row.receipt_type),
        row.parser_version,
        ReviewStatus(row.review_status),
        row.duplicate_of_candidate_id,
        row.duplicate_reason,
        row.amount,
        row.currency,
        row.transaction_occurred_at,
        row.timezone_state,
        row.reference_original,
        row.reference_normalized,
        row.sender_name,
        row.recipient_name,
        row.recipient_identifier,
        row.transfer_status,
        cast(dict[str, JsonValue], row.machine_result),
        cast(dict[str, JsonValue] | None, row.reviewed_result),
        aware(row.created_at),
        aware(row.updated_at),
        aware(row.expires_at),
        cast(dict[str, JsonValue] | None, row.resolution_result),
    )


class SqlProcessingRepository:
    async def nearby_sms(self, device_id: UUID, timestamp: datetime) -> list[RawEvent]:
        rows = await self.db.scalars(
            select(RawEventRow)
            .where(
                RawEventRow.device_id == device_id,
                RawEventRow.collector == "sms",
                RawEventRow.occurred_at >= timestamp - timedelta(hours=24),
                RawEventRow.occurred_at <= timestamp + timedelta(hours=24),
                RawEventRow.delete_after > timestamp,
            )
            .order_by(RawEventRow.occurred_at, RawEventRow.id)
            .limit(200)
        )
        return [to_event(row) for row in rows]

    async def exception_command(self, command_id: UUID) -> dict[str, JsonValue] | None:
        row = await self.db.get(ReceiptExceptionCommandRow, command_id)
        if row is None:
            return None
        return {
            "candidate_id": str(row.candidate_id),
            "reviewer_id": str(row.reviewer_id),
            "body_digest": row.body_digest,
            "response": cast(dict[str, JsonValue], row.response),
        }

    async def save_exception_command(
        self,
        command_id: UUID,
        candidate: ReceiptCandidate,
        reviewer_id: UUID,
        body_digest: str,
        response: dict[str, JsonValue],
        now: datetime,
    ) -> None:
        self.db.add(
            ReceiptExceptionCommandRow(
                id=command_id,
                candidate_id=candidate.id,
                reviewer_id=reviewer_id,
                body_digest=body_digest,
                response=response,
                created_at=now,
                expires_at=candidate.expires_at,
            )
        )
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            raise Conflict(
                "Exception command identifier conflicts with another command."
            ) from None

    def __init__(self, session: AsyncSession) -> None:
        self.db = session

    async def source_for_object(
        self, object_id: UUID
    ) -> tuple[UUID, UUID, datetime] | None:
        row = await self.db.execute(
            select(RawEventRow.id, RawEventRow.device_id, RawEventRow.delete_after)
            .where(RawEventRow.object_id == object_id)
            .order_by(RawEventRow.received_at)
            .limit(1)
        )
        value = row.one_or_none()
        return (value[0], value[1], aware(value[2])) if value else None

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
    ) -> ProcessingJob:
        existing = await self.db.scalar(
            select(ProcessingJobRow).where(
                ProcessingJobRow.object_id == object_id,
                ProcessingJobRow.processor_version == processor_version,
                ProcessingJobRow.parser_type == parser_type,
                ProcessingJobRow.parser_version == parser_version,
            )
        )
        if existing is not None:
            return to_job(existing)
        row = ProcessingJobRow(
            id=uuid4(),
            object_id=object_id,
            raw_event_id=raw_event_id,
            device_id=device_id,
            processor_version=processor_version,
            parser_type=parser_type,
            parser_version=parser_version,
            ocr_provider=ocr_provider,
            requested_languages=list(requested_languages),
            status=JobStatus.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=now,
            started_at=None,
            completed_at=None,
            failure_code=None,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError:
            raced = await self.db.scalar(
                select(ProcessingJobRow).where(
                    ProcessingJobRow.object_id == object_id,
                    ProcessingJobRow.processor_version == processor_version,
                    ProcessingJobRow.parser_type == parser_type,
                    ProcessingJobRow.parser_version == parser_version,
                )
            )
            if raced is None:
                raise
            return to_job(raced)
        return to_job(row)

    async def claim_next(
        self, now: datetime, stale_before: datetime
    ) -> tuple[ProcessingJob, UUID] | None:
        row = await self.db.scalar(
            select(ProcessingJobRow)
            .where(
                ProcessingJobRow.attempt_count < ProcessingJobRow.max_attempts,
                or_(
                    (
                        (ProcessingJobRow.status == JobStatus.PENDING.value)
                        & (ProcessingJobRow.next_attempt_at <= now)
                    ),
                    (
                        (ProcessingJobRow.status == JobStatus.PROCESSING.value)
                        & (ProcessingJobRow.updated_at <= stale_before)
                    ),
                ),
            )
            .order_by(ProcessingJobRow.next_attempt_at, ProcessingJobRow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        if row.status == JobStatus.PROCESSING.value:
            await self.db.execute(
                update(ProcessingAttemptRow)
                .where(
                    ProcessingAttemptRow.job_id == row.id,
                    ProcessingAttemptRow.status == AttemptStatus.PROCESSING.value,
                )
                .values(
                    status=AttemptStatus.FAILED.value,
                    failure_code=ProcessingFailure.INTERNAL_ERROR.value,
                    completed_at=now,
                )
            )
        row.attempt_count += 1
        row.status = JobStatus.PROCESSING.value
        row.started_at = now
        row.updated_at = now
        row.failure_code = None
        attempt_id = uuid4()
        self.db.add(
            ProcessingAttemptRow(
                id=attempt_id,
                job_id=row.id,
                attempt_number=row.attempt_count,
                status=AttemptStatus.PROCESSING.value,
                provider=row.ocr_provider,
                provider_version=None,
                failure_code=None,
                started_at=now,
                completed_at=None,
                duration_ms=None,
                expires_at=row.expires_at,
            )
        )
        await self.db.flush()
        return to_job(row), attempt_id

    async def job(self, job_id: UUID) -> ProcessingJob | None:
        row = await self.db.get(ProcessingJobRow, job_id)
        return to_job(row) if row else None

    async def list_jobs(
        self,
        *,
        status: str | None,
        object_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[ProcessingJob]:
        query = select(ProcessingJobRow)
        if status is not None:
            query = query.where(ProcessingJobRow.status == status)
        if object_id is not None:
            query = query.where(ProcessingJobRow.object_id == object_id)
        rows = await self.db.scalars(
            query.order_by(ProcessingJobRow.created_at.desc(), ProcessingJobRow.id)
            .limit(limit)
            .offset(offset)
        )
        return [to_job(row) for row in rows]

    async def attempts(self, job_id: UUID) -> list[dict[str, JsonValue]]:
        rows = await self.db.scalars(
            select(ProcessingAttemptRow)
            .where(ProcessingAttemptRow.job_id == job_id)
            .order_by(ProcessingAttemptRow.attempt_number)
        )
        return [
            {
                "id": str(row.id),
                "attempt_number": row.attempt_number,
                "status": row.status,
                "provider": row.provider,
                "provider_version": row.provider_version,
                "failure_code": row.failure_code,
                "started_at": aware(row.started_at).isoformat(),
                "completed_at": (
                    aware(row.completed_at).isoformat() if row.completed_at else None
                ),
                "duration_ms": row.duration_ms,
            }
            for row in rows
        ]

    async def owns_attempt(self, job: ProcessingJob, attempt_id: UUID) -> bool:
        current = await self.db.scalar(
            select(ProcessingJobRow.id)
            .join(
                ProcessingAttemptRow,
                ProcessingAttemptRow.job_id == ProcessingJobRow.id,
            )
            .where(
                ProcessingJobRow.id == job.id,
                ProcessingJobRow.status == JobStatus.PROCESSING.value,
                ProcessingJobRow.attempt_count == job.attempt_count,
                ProcessingAttemptRow.id == attempt_id,
                ProcessingAttemptRow.attempt_number == job.attempt_count,
                ProcessingAttemptRow.status == AttemptStatus.PROCESSING.value,
            )
            .with_for_update()
        )
        return current is not None

    async def save_success(
        self,
        job: ProcessingJob,
        attempt_id: UUID,
        document: OcrDocument,
        parsed: ParsedReceipt,
        duplicate_of: UUID | None,
        now: datetime,
    ) -> ReceiptCandidate | None:
        if not await self.owns_attempt(job, attempt_id):
            return None
        if (
            parsed.evidence_result is not None
            and len(json.dumps(parsed.evidence_result).encode()) > 256 * 1024
        ):
            from relay.application.processing_errors import ProcessingError

            raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
        ocr_id, candidate_id = uuid4(), uuid4()
        self.db.add(
            OcrResultRow(
                id=ocr_id,
                job_id=job.id,
                object_id=job.object_id,
                provider=document.provider_id,
                provider_version=document.provider_version,
                processor_version=job.processor_version,
                requested_languages=list(document.requested_languages),
                available_languages=list(document.available_languages),
                normalized_text=document.normalized_text,
                blocks=[line.as_json() for line in document.evidence_lines],
                mean_confidence=(
                    Decimal(str(document.mean_confidence))
                    if document.mean_confidence is not None
                    else None
                ),
                duration_ms=document.duration_ms,
                created_at=now,
                expires_at=job.expires_at,
            )
        )
        # All new machine output is suggestive. Neither a validated OCR region
        # nor a matching SMS promotes financial fields in Sprint 8.
        review_status = (
            ReviewStatus.REJECTED
            if parsed.receipt_type == ReceiptType.UNKNOWN_RECEIPT
            or (
                isinstance(parsed.evidence_result, dict)
                and parsed.evidence_result.get("state") == "TRANSFER_FAILED"
            )
            else ReviewStatus.NEEDS_REVIEW
        )
        machine_result = parsed.machine_result()
        resolution = initial_resolution(
            machine_result,
            supported_receipt=parsed.receipt_type != ReceiptType.UNKNOWN_RECEIPT,
        )
        row = ReceiptCandidateRow(
            id=candidate_id,
            job_id=job.id,
            object_id=job.object_id,
            raw_event_id=job.raw_event_id,
            device_id=job.device_id,
            receipt_type=parsed.receipt_type.value,
            parser_version=parsed.parser_version,
            review_status=review_status.value,
            duplicate_of_candidate_id=None,
            duplicate_reason=None,
            amount=None,
            currency=None,
            transaction_occurred_at=None,
            timezone_state=parsed.timezone_state,
            reference_original=None,
            reference_normalized=None,
            sender_name=None,
            recipient_name=None,
            recipient_identifier=None,
            transfer_status=None,
            machine_result=machine_result,
            reviewed_result=None,
            resolution_result=resolution,
            created_at=now,
            updated_at=now,
            expires_at=job.expires_at,
        )
        self.db.add(row)
        if parsed.evidence_result is not None:
            self.db.add(
                ReceiptEvidenceRow(
                    candidate_id=candidate_id,
                    schema_version=1,
                    payload=parsed.evidence_result,
                    created_at=now,
                    expires_at=job.expires_at,
                )
            )
        final_status = (
            JobStatus.SKIPPED
            if parsed.receipt_type == ReceiptType.UNKNOWN_RECEIPT
            else (
                JobStatus.NEEDS_REVIEW
                if review_status == ReviewStatus.NEEDS_REVIEW
                else JobStatus.SUCCEEDED
            )
        )
        await self.db.execute(
            update(ProcessingJobRow)
            .where(
                ProcessingJobRow.id == job.id,
                ProcessingJobRow.status == JobStatus.PROCESSING.value,
                ProcessingJobRow.attempt_count == job.attempt_count,
            )
            .values(
                status=final_status.value,
                completed_at=now,
                updated_at=now,
                failure_code=(
                    ProcessingFailure.UNKNOWN_RECEIPT.value
                    if final_status == JobStatus.SKIPPED
                    else None
                ),
            )
        )
        await self.db.execute(
            update(ProcessingAttemptRow)
            .where(
                ProcessingAttemptRow.id == attempt_id,
                ProcessingAttemptRow.status == AttemptStatus.PROCESSING.value,
            )
            .values(
                status=AttemptStatus.SUCCEEDED.value,
                provider_version=document.provider_version,
                completed_at=now,
                duration_ms=document.duration_ms,
            )
        )
        await self.db.flush()
        return to_candidate(row)

    async def save_failure(
        self,
        job: ProcessingJob,
        attempt_id: UUID,
        failure: ProcessingFailure,
        *,
        transient: bool,
        now: datetime,
    ) -> bool:
        if not await self.owns_attempt(job, attempt_id):
            return False
        retry = transient and job.attempt_count < job.max_attempts
        await self.db.execute(
            update(ProcessingAttemptRow)
            .where(
                ProcessingAttemptRow.id == attempt_id,
                ProcessingAttemptRow.status == AttemptStatus.PROCESSING.value,
            )
            .values(
                status=AttemptStatus.FAILED.value,
                failure_code=failure.value,
                completed_at=now,
            )
        )
        await self.db.execute(
            update(ProcessingJobRow)
            .where(
                ProcessingJobRow.id == job.id,
                ProcessingJobRow.status == JobStatus.PROCESSING.value,
                ProcessingJobRow.attempt_count == job.attempt_count,
            )
            .values(
                status=JobStatus.PENDING.value if retry else JobStatus.FAILED.value,
                next_attempt_at=now + timedelta(minutes=2 ** (job.attempt_count - 1)),
                completed_at=None if retry else now,
                updated_at=now,
                failure_code=failure.value,
            )
        )
        return True

    async def canonical_reference(
        self,
        device_id: UUID,
        receipt_type: str,
        normalized_reference: str,
        *,
        exclude_candidate_id: UUID | None = None,
    ) -> UUID | None:
        query = select(ReceiptCandidateRow).where(
            ReceiptCandidateRow.device_id == device_id,
            ReceiptCandidateRow.receipt_type == receipt_type,
            ReceiptCandidateRow.reference_normalized == normalized_reference,
            ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
        )
        if exclude_candidate_id is not None:
            query = query.where(ReceiptCandidateRow.id != exclude_candidate_id)
        rows = await self.db.scalars(
            query.order_by(ReceiptCandidateRow.created_at, ReceiptCandidateRow.id)
        )
        for row in rows:
            if (
                reviewed_value(row.resolution_result, "reference_number")
                == normalized_reference
            ):
                return row.id
        return None

    async def untrusted_reference_collision(
        self,
        device_id: UUID,
        receipt_type: str,
        normalized_reference: str,
        *,
        exclude_candidate_id: UUID,
    ) -> bool:
        rows = await self.db.scalars(
            select(ReceiptCandidateRow).where(
                ReceiptCandidateRow.device_id == device_id,
                ReceiptCandidateRow.receipt_type == receipt_type,
                ReceiptCandidateRow.reference_normalized == normalized_reference,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                ReceiptCandidateRow.id != exclude_candidate_id,
            )
        )
        return any(
            reviewed_value(row.resolution_result, "reference_number")
            != normalized_reference
            for row in rows
        )

    async def lock_device(self, device_id: UUID) -> None:
        found = await self.db.scalar(
            select(DeviceRow.id).where(DeviceRow.id == device_id).with_for_update()
        )
        if found is None:
            raise RuntimeError("Processing job references a missing device.")

    async def candidate(
        self, candidate_id: UUID, *, for_update: bool = False
    ) -> ReceiptCandidate | None:
        query = select(ReceiptCandidateRow).where(
            ReceiptCandidateRow.id == candidate_id
        )
        if for_update:
            query = query.with_for_update()
        row = await self.db.scalar(query)
        return to_candidate(row) if row else None

    async def list_candidates(
        self,
        *,
        review_status: str | None,
        receipt_type: str | None,
        device_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[ReceiptCandidate]:
        query = select(ReceiptCandidateRow)
        if review_status is not None:
            query = query.where(ReceiptCandidateRow.review_status == review_status)
        if receipt_type is not None:
            query = query.where(ReceiptCandidateRow.receipt_type == receipt_type)
        if device_id is not None:
            query = query.where(ReceiptCandidateRow.device_id == device_id)
        rows = await self.db.scalars(
            query.order_by(
                ReceiptCandidateRow.created_at.desc(), ReceiptCandidateRow.id
            )
            .limit(limit)
            .offset(offset)
        )
        return [to_candidate(row) for row in rows]

    async def next_exception(self) -> ReceiptCandidate | None:
        row = await self.db.scalar(
            select(ReceiptCandidateRow)
            .where(
                or_(
                    ReceiptCandidateRow.review_status
                    == ReviewStatus.NEEDS_REVIEW.value,
                    (
                        (
                            ReceiptCandidateRow.review_status
                            == ReviewStatus.AUTO_ACCEPTED.value
                        )
                        & ReceiptCandidateRow.resolution_result.is_(None)
                        & (
                            ReceiptCandidateRow.receipt_type
                            == ReceiptType.INSTAPAY_TRANSFER_RECEIPT.value
                        )
                    ),
                )
            )
            .order_by(ReceiptCandidateRow.created_at, ReceiptCandidateRow.id)
            .limit(1)
        )
        return to_candidate(row) if row else None

    async def ocr_for_candidate(
        self, candidate_id: UUID
    ) -> dict[str, JsonValue] | None:
        row = await self.db.scalar(
            select(OcrResultRow)
            .join(
                ReceiptCandidateRow, ReceiptCandidateRow.job_id == OcrResultRow.job_id
            )
            .where(ReceiptCandidateRow.id == candidate_id)
        )
        if row is None:
            return None
        return {
            "id": str(row.id),
            "provider": row.provider,
            "provider_version": row.provider_version,
            "processor_version": row.processor_version,
            "requested_languages": cast(list[JsonValue], row.requested_languages),
            "available_languages": cast(list[JsonValue], row.available_languages),
            "normalized_text": row.normalized_text,
            "blocks": cast(list[JsonValue], row.blocks),
            "mean_confidence": (
                float(row.mean_confidence) if row.mean_confidence is not None else None
            ),
            "duration_ms": row.duration_ms,
            "created_at": aware(row.created_at).isoformat(),
            "expires_at": aware(row.expires_at).isoformat(),
        }

    async def audit_for_candidate(
        self, candidate_id: UUID
    ) -> list[dict[str, JsonValue]]:
        rows = await self.db.scalars(
            select(ReceiptReviewAuditRow)
            .where(ReceiptReviewAuditRow.candidate_id == candidate_id)
            .order_by(ReceiptReviewAuditRow.created_at, ReceiptReviewAuditRow.id)
        )
        return [
            {
                "id": str(row.id),
                "reviewer_id": str(row.reviewer_id),
                "action": row.action,
                "before_result": cast(dict[str, JsonValue], row.before_result),
                "after_result": cast(dict[str, JsonValue], row.after_result),
                "created_at": aware(row.created_at).isoformat(),
            }
            for row in rows
        ]

    async def reviewed_fields_for_candidate(
        self, candidate_id: UUID
    ) -> list[dict[str, JsonValue]]:
        rows = await self.db.scalars(
            select(ReceiptReviewedFieldRow)
            .where(ReceiptReviewedFieldRow.candidate_id == candidate_id)
            .order_by(ReceiptReviewedFieldRow.created_at, ReceiptReviewedFieldRow.id)
        )
        return [
            {
                "id": str(row.id),
                "command_id": str(row.command_id),
                "reviewer_id": str(row.reviewer_id),
                "field": row.field,
                "action": row.action,
                "machine_candidates": cast(list[JsonValue], row.machine_candidates),
                "reviewed_value": row.reviewed_value,
                "reason": row.reason,
                "provenance": cast(dict[str, JsonValue], row.provenance),
                "created_at": aware(row.created_at).isoformat(),
            }
            for row in rows
        ]

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
    ) -> None:
        self.db.add(
            ReceiptReviewedFieldRow(
                id=uuid4(),
                candidate_id=candidate.id,
                command_id=command_id,
                reviewer_id=reviewer_id,
                field=field,
                action=action,
                machine_candidates=candidates,
                reviewed_value=value,
                reason=reason,
                provenance=provenance,
                created_at=now,
                expires_at=candidate.expires_at,
            )
        )
        await self.db.flush()

    async def review_metrics(self) -> dict[str, JsonValue]:
        candidates = list(await self.db.scalars(select(ReceiptCandidateRow)))
        actions = list(await self.db.scalars(select(ReceiptReviewedFieldRow)))
        job_failures = await self.db.scalar(
            select(func.count())
            .select_from(ProcessingJobRow)
            .where(ProcessingJobRow.status == JobStatus.FAILED.value)
        )
        by_field: dict[str, dict[str, int]] = {}
        per_candidate: dict[UUID, int] = {}
        for row in actions:
            counts = by_field.setdefault(
                row.field, {"confirmations": 0, "corrections": 0, "unavailable": 0}
            )
            key = {
                "CONFIRM": "confirmations",
                "CORRECT": "corrections",
                "UNAVAILABLE": "unavailable",
            }[row.action]
            counts[key] += 1
            per_candidate[row.candidate_id] = per_candidate.get(row.candidate_id, 0) + 1
        exceptions = [
            row
            for row in candidates
            if isinstance(row.resolution_result, dict)
            and row.resolution_result.get("state") == "NEEDS_REVIEW"
        ]
        blocking_count = sum(
            len(row.resolution_result.get("blocking_fields", []))
            for row in exceptions
            if isinstance(row.resolution_result, dict)
        )
        reviewed_count = len(per_candidate)
        return {
            "total_candidates": len(candidates),
            "needs_review": len(exceptions),
            "finalized_after_review": sum(
                isinstance(row.resolution_result, dict)
                and row.resolution_result.get("state") == "REVIEW_FINALIZED"
                for row in candidates
            ),
            "rejected": sum(
                row.review_status == ReviewStatus.REJECTED.value for row in candidates
            ),
            "processing_failed": int(job_failures or 0),
            "transfer_failed": sum(
                isinstance(row.resolution_result, dict)
                and row.resolution_result.get("state") == "TRANSFER_FAILED"
                for row in candidates
            ),
            "fields_requiring_review": blocking_count,
            "average_fields_per_exception": (
                blocking_count / len(exceptions) if exceptions else 0
            ),
            "average_operator_actions_per_reviewed_transaction": (
                len(actions) / reviewed_count if reviewed_count else 0
            ),
            "by_field": by_field,
            "duplicate_links_after_review": sum(
                row.duplicate_of_candidate_id is not None
                and reviewed_value(row.resolution_result, "reference_number")
                is not None
                for row in candidates
            ),
        }

    async def review(
        self,
        candidate: ReceiptCandidate,
        reviewer_id: UUID,
        action: str,
        before: dict[str, JsonValue],
        after: dict[str, JsonValue],
        now: datetime,
    ) -> None:
        await self.db.execute(
            update(ProcessingJobRow)
            .where(ProcessingJobRow.id == candidate.job_id)
            .values(
                status=(
                    JobStatus.SUCCEEDED.value
                    if candidate.review_status == ReviewStatus.AUTO_ACCEPTED
                    else (
                        JobStatus.SKIPPED.value
                        if candidate.review_status == ReviewStatus.REJECTED
                        else JobStatus.NEEDS_REVIEW.value
                    )
                ),
                updated_at=now,
            )
        )
        await self.db.execute(
            update(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.id == candidate.id)
            .values(
                review_status=candidate.review_status.value,
                duplicate_of_candidate_id=candidate.duplicate_of_candidate_id,
                duplicate_reason=candidate.duplicate_reason,
                amount=candidate.amount,
                currency=candidate.currency,
                transaction_occurred_at=candidate.transaction_occurred_at,
                timezone_state=candidate.timezone_state,
                reference_original=candidate.reference_original,
                reference_normalized=candidate.reference_normalized,
                sender_name=candidate.sender_name,
                recipient_name=candidate.recipient_name,
                recipient_identifier=candidate.recipient_identifier,
                transfer_status=candidate.transfer_status,
                reviewed_result=candidate.reviewed_result,
                resolution_result=candidate.resolution_result,
                updated_at=now,
            )
        )
        self.db.add(
            ReceiptReviewAuditRow(
                id=uuid4(),
                candidate_id=candidate.id,
                reviewer_id=reviewer_id,
                action=action,
                before_result=before,
                after_result=after,
                created_at=now,
                expires_at=candidate.expires_at,
            )
        )
        await self.db.flush()

    async def purge_expired(self, before: datetime) -> int:
        # Explicit child retention also works in disposable SQLite databases
        # whose connection does not enable foreign-key cascade enforcement.
        await self.db.execute(
            delete(ReceiptExceptionCommandRow).where(
                ReceiptExceptionCommandRow.expires_at <= before
            )
        )
        await self.db.execute(
            delete(ReceiptReviewedFieldRow).where(
                ReceiptReviewedFieldRow.expires_at <= before
            )
        )
        await self.db.execute(
            delete(ReceiptEvidenceRow).where(ReceiptEvidenceRow.expires_at <= before)
        )
        await self._promote_surviving_duplicates(before)
        await self.db.execute(
            delete(ReceiptCandidateRow).where(
                ReceiptCandidateRow.expires_at <= before,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_not(None),
            )
        )
        await self.db.execute(
            delete(ReceiptCandidateRow).where(
                ReceiptCandidateRow.expires_at <= before,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
            )
        )
        result = cast(
            CursorResult[tuple[()]],
            await self.db.execute(
                delete(ProcessingJobRow).where(ProcessingJobRow.expires_at <= before)
            ),
        )
        return int(result.rowcount or 0)

    async def _promote_surviving_duplicates(self, before: datetime) -> None:
        canonicals = list(
            await self.db.scalars(
                select(ReceiptCandidateRow)
                .where(
                    ReceiptCandidateRow.expires_at <= before,
                    ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                )
                .order_by(ReceiptCandidateRow.created_at, ReceiptCandidateRow.id)
                .with_for_update()
            )
        )
        for canonical in canonicals:
            duplicates = list(
                await self.db.scalars(
                    select(ReceiptCandidateRow)
                    .where(
                        ReceiptCandidateRow.duplicate_of_candidate_id == canonical.id
                    )
                    .order_by(ReceiptCandidateRow.created_at, ReceiptCandidateRow.id)
                    .with_for_update()
                )
            )
            survivors = [row for row in duplicates if row.expires_at > before]
            if not survivors:
                continue
            promoted = survivors[0]
            await self.db.execute(
                update(ReceiptCandidateRow)
                .where(
                    ReceiptCandidateRow.duplicate_of_candidate_id == canonical.id,
                    ReceiptCandidateRow.id != promoted.id,
                )
                .values(duplicate_of_candidate_id=promoted.id)
            )
            await self.db.execute(
                delete(ReceiptCandidateRow).where(
                    ReceiptCandidateRow.id == canonical.id
                )
            )
            await self.db.execute(
                update(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == promoted.id)
                .values(
                    duplicate_of_candidate_id=None,
                    duplicate_reason=None,
                )
            )
