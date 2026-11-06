"""Durable asynchronous receipt-processing use cases."""

import copy
import hashlib
import json
import logging
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from relay.application.errors import Conflict, NotFound
from relay.application.evidence_ports import (
    EVIDENCE_PARSER_TYPE,
    EVIDENCE_PARSER_VERSION,
    EVIDENCE_PROCESSOR_VERSION,
    EvidenceExtractor,
)
from relay.application.processing_errors import ProcessingError
from relay.application.processing_ports import (
    ImagePreprocessor,
    ObjectReader,
    OcrProvider,
    ProcessingUnitOfWork,
    ReceiptParser,
)
from relay.application.receipts import normalize_reference
from relay.domain.agent import JsonValue
from relay.domain.binary_objects import MAX_IMAGE_BYTES
from relay.domain.evidence import CRITICAL_FIELDS
from relay.domain.processing import (
    DEFAULT_OCR_LANGUAGES,
    DEFAULT_OCR_PROVIDER,
    DEFAULT_PARSER_TYPE,
    DEFAULT_PARSER_VERSION,
    DEFAULT_PROCESSOR_VERSION,
    MAX_OCR_BLOCKS,
    MAX_OCR_TEXT_BYTES,
    OcrDocument,
    OcrLineOrigin,
    ProcessingFailure,
    ProcessingJob,
    ReceiptCandidate,
    ReceiptType,
    ReviewStatus,
)
from relay.domain.review_trust import (
    OPTIONAL_FIELDS,
    FieldTrust,
    initial_resolution,
    reviewed_value,
    unresolved_required_fields,
)

logger = logging.getLogger(__name__)
VERSION = re.compile(r"[a-z][a-z0-9_.-]{0,31}(?::v[1-9][0-9]{0,5})?", re.ASCII)
REVIEW_ACTIONS = {"ACCEPT", "CORRECT", "REJECT"}
CORRECTION_FIELDS = {
    "amount",
    "currency",
    "transaction_occurred_at",
    "timezone_state",
    "reference_number",
    "sender_name",
    "recipient_name",
    "recipient_identifier",
    "transfer_status",
}


class ProcessingService:
    def __init__(
        self,
        uow: ProcessingUnitOfWork,
        storage: ObjectReader,
        preprocessor: ImagePreprocessor | None = None,
        ocr: OcrProvider | None = None,
        parser: ReceiptParser | None = None,
        *,
        ocr_timeout_seconds: float = 20,
        stale_job_minutes: int = 5,
        evidence_extractor: EvidenceExtractor | None = None,
    ) -> None:
        self.uow = uow
        self.storage = storage
        self.preprocessor = preprocessor
        self.ocr = ocr
        self.parser = parser
        self.ocr_timeout_seconds = ocr_timeout_seconds
        self.stale_job_minutes = stale_job_minutes
        self.evidence_extractor = evidence_extractor

    async def enqueue(
        self,
        object_id: UUID,
        *,
        processor_version: str = DEFAULT_PROCESSOR_VERSION,
        parser_type: str = DEFAULT_PARSER_TYPE,
        parser_version: str = DEFAULT_PARSER_VERSION,
        requested_languages: tuple[str, ...] = DEFAULT_OCR_LANGUAGES,
    ) -> ProcessingJob:
        if (
            VERSION.fullmatch(processor_version) is None
            or VERSION.fullmatch(parser_type) is None
            or VERSION.fullmatch(parser_version) is None
            or not requested_languages
            or len(requested_languages) > 4
        ):
            raise Conflict("Unsupported processing version or language configuration.")
        source = await self.uow.processing.source_for_object(object_id)
        if source is None:
            raise NotFound("Finalized image event not found.")
        raw_event_id, device_id, expires_at = source
        now = datetime.now(UTC)
        value = await self.uow.processing.ensure_job(
            object_id=object_id,
            raw_event_id=raw_event_id,
            device_id=device_id,
            processor_version=processor_version,
            parser_type=parser_type,
            parser_version=parser_version,
            ocr_provider=self.ocr.provider_id if self.ocr else DEFAULT_OCR_PROVIDER,
            requested_languages=requested_languages,
            expires_at=expires_at,
            now=now,
        )
        await self.uow.commit()
        return value

    async def process_pending(self, *, limit: int = 10) -> list[UUID]:
        processed: list[UUID] = []
        for _ in range(max(1, min(limit, 100))):
            value = await self.process_one()
            if value is None:
                break
            processed.append(value)
        return processed

    async def process_one(self) -> UUID | None:
        if self.preprocessor is None or self.ocr is None or self.parser is None:
            raise RuntimeError("OCR processing components are not configured.")
        preprocessor = self.preprocessor
        ocr = self.ocr
        parser = self.parser
        now = datetime.now(UTC)
        claimed = await self.uow.processing.claim_next(
            now, now - timedelta(minutes=self.stale_job_minutes)
        )
        if claimed is None:
            await self.uow.rollback()
            return None
        job, attempt_id = claimed
        await self.uow.commit()
        try:
            if job.ocr_provider != ocr.provider_id:
                raise ProcessingError(ProcessingFailure.OCR_FAILED)
            if job.processor_version != EVIDENCE_PROCESSOR_VERSION and (
                job.parser_type != parser.parser_type
                or job.parser_version != parser.parser_version
            ):
                raise ProcessingError(ProcessingFailure.PARSER_FAILED)
            binary = await self.uow.binaries.object(job.object_id)
            if binary is None:
                raise ProcessingError(ProcessingFailure.UNSUPPORTED_IMAGE)
            data = self.storage.read(binary.storage_key, MAX_IMAGE_BYTES)
            if job.processor_version == EVIDENCE_PROCESSOR_VERSION:
                if self.evidence_extractor is None or (
                    job.parser_type,
                    job.parser_version,
                ) != (EVIDENCE_PARSER_TYPE, EVIDENCE_PARSER_VERSION):
                    raise ProcessingError(ProcessingFailure.PARSER_FAILED)
                from relay.application.evidence_product import resolve_evidence
                from relay.application.evidence_sources import (
                    correlate_receipt_sources,
                    parse_explicit_sms,
                )

                sms_groups = []
                for event in await self.uow.processing.nearby_sms(
                    job.device_id, job.created_at
                ):
                    sender, body = (
                        event.payload.get("sender"),
                        event.payload.get("body"),
                    )
                    if isinstance(sender, str) and isinstance(body, str):
                        sms_groups.append(
                            parse_explicit_sms(
                                str(event.id),
                                sender,
                                body,
                                event.occurred_at.isoformat(),
                                event.content_hash,
                            )
                        )
                evidence = await self.evidence_extractor.extract(
                    data,
                    source_event_id=str(job.raw_event_id),
                    timestamp=job.created_at.isoformat(),
                    requested_languages=job.requested_languages,
                    timeout_seconds=self.ocr_timeout_seconds,
                    structured_sources=tuple(sms_groups),
                )
                document = evidence.document
                _validate_document(
                    document,
                    job,
                    allow_empty=(
                        not evidence.supported_layout
                        and not evidence.classification_resolved
                        and not document.evidence_lines
                        and evidence.metadata.get("recognition_skipped")
                        == "visual_registration_not_admitted"
                    ),
                )
                structured = correlate_receipt_sources(
                    evidence.candidates, tuple(sms_groups)
                )
                parsed = resolve_evidence(evidence, structured_candidates=structured)
            else:
                if (job.parser_type, job.parser_version) != (
                    parser.parser_type,
                    parser.parser_version,
                ):
                    raise ProcessingError(ProcessingFailure.PARSER_FAILED)
                image = preprocessor.preprocess(data)
                document = await ocr.extract(
                    image, job.requested_languages, self.ocr_timeout_seconds
                )
                _validate_document(document, job)
                parsed = parser.parse(document.parser_view())
            await self.uow.processing.lock_device(job.device_id)
            # Machine-only references never create canonical relationships.
            duplicate_of = None
            saved = await self.uow.processing.save_success(
                job, attempt_id, document, parsed, duplicate_of, datetime.now(UTC)
            )
            if saved is None:
                await self.uow.rollback()
                logger.info(
                    "processing.stale_result_discarded",
                    extra={"job_id": str(job.id), "attempt_id": str(attempt_id)},
                )
                return job.id
            await self.uow.commit()
            logger.info(
                "processing.completed",
                extra={
                    "job_id": str(job.id),
                    "object_id": str(job.object_id),
                    "status": parsed.review_status.value,
                    "duplicate": duplicate_of is not None,
                },
            )
        except ProcessingError as exc:
            await self._record_failure(job, attempt_id, exc.failure, exc.transient)
        except Exception:
            await self.uow.rollback()
            await self._record_failure(
                job, attempt_id, ProcessingFailure.INTERNAL_ERROR, False
            )
        return job.id

    async def _record_failure(
        self,
        job: ProcessingJob,
        attempt_id: UUID,
        failure: ProcessingFailure,
        transient: bool,
    ) -> None:
        saved = await self.uow.processing.save_failure(
            job, attempt_id, failure, transient=transient, now=datetime.now(UTC)
        )
        if not saved:
            await self.uow.rollback()
            logger.info(
                "processing.stale_failure_discarded",
                extra={"job_id": str(job.id), "attempt_id": str(attempt_id)},
            )
            return
        await self.uow.commit()
        logger.warning(
            "processing.failed",
            extra={
                "job_id": str(job.id),
                "object_id": str(job.object_id),
                "failure_code": failure.value,
                "retryable": transient and job.attempt_count < job.max_attempts,
            },
        )

    async def get_job(
        self, job_id: UUID
    ) -> tuple[ProcessingJob, list[dict[str, JsonValue]]]:
        value = await self.uow.processing.job(job_id)
        if value is None:
            raise NotFound("Processing job not found.")
        return value, await self.uow.processing.attempts(job_id)

    async def list_jobs(
        self,
        *,
        status: str | None,
        object_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[ProcessingJob]:
        return await self.uow.processing.list_jobs(
            status=status, object_id=object_id, limit=limit, offset=offset
        )

    async def get_candidate(
        self, candidate_id: UUID
    ) -> tuple[
        ReceiptCandidate,
        dict[str, JsonValue],
        list[dict[str, JsonValue]],
        list[dict[str, JsonValue]],
    ]:
        candidate = await self.uow.processing.candidate(candidate_id)
        if candidate is None:
            raise NotFound("Receipt candidate not found.")
        ocr = await self.uow.processing.ocr_for_candidate(candidate_id)
        if ocr is None:
            raise NotFound("OCR result not found.")
        return (
            candidate,
            ocr,
            await self.uow.processing.audit_for_candidate(candidate_id),
            await self.uow.processing.reviewed_fields_for_candidate(candidate_id),
        )

    async def review_metrics(self) -> dict[str, JsonValue]:
        return await self.uow.processing.review_metrics()

    async def next_exception(self) -> ReceiptCandidate | None:
        return await self.uow.processing.next_exception()

    async def list_candidates(
        self,
        *,
        review_status: str | None,
        receipt_type: str | None,
        device_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[ReceiptCandidate]:
        return await self.uow.processing.list_candidates(
            review_status=review_status,
            receipt_type=receipt_type,
            device_id=device_id,
            limit=limit,
            offset=offset,
        )

    async def review(
        self,
        candidate_id: UUID,
        reviewer_id: UUID,
        action: str,
        corrections: dict[str, JsonValue],
    ) -> ReceiptCandidate:
        # The old whole-record mutation cannot express field-level trust or a
        # revision. Keep only its non-financial reject operation for old clients.
        if action != "REJECT":
            raise Conflict("Use field-level exception review for transaction values.")
        if action not in REVIEW_ACTIONS or not set(corrections) <= CORRECTION_FIELDS:
            raise Conflict("Invalid review action.")
        if (action == "CORRECT") != bool(corrections):
            raise Conflict("Corrections are required only for CORRECT actions.")
        initial = await self.uow.processing.candidate(candidate_id)
        if initial is None:
            raise NotFound("Receipt candidate not found.")
        await self.uow.processing.lock_device(initial.device_id)
        candidate = await self.uow.processing.candidate(candidate_id, for_update=True)
        if candidate is None:
            raise NotFound("Receipt candidate not found.")
        before = _candidate_snapshot(candidate)
        if action == "CORRECT":
            _apply_corrections(candidate, corrections)
        candidate.review_status = (
            ReviewStatus.REJECTED
            if action == "REJECT"
            else (
                ReviewStatus.AUTO_ACCEPTED
                if candidate.amount is not None
                and candidate.currency is not None
                and candidate.reference_normalized is not None
                else ReviewStatus.NEEDS_REVIEW
            )
        )
        await self.uow.processing.lock_device(candidate.device_id)
        if not (
            action == "REJECT"
            and isinstance(candidate.machine_result.get("evidence_first"), dict)
        ):
            candidate.duplicate_of_candidate_id = None
            candidate.duplicate_reason = None
        if candidate.reference_normalized and action != "REJECT":
            duplicate = await self.uow.processing.canonical_reference(
                candidate.device_id,
                candidate.receipt_type.value,
                candidate.reference_normalized,
                exclude_candidate_id=candidate.id,
            )
            if duplicate is not None:
                candidate.duplicate_of_candidate_id = duplicate
                candidate.duplicate_reason = "REFERENCE_NUMBER"
                if action != "ACCEPT":
                    candidate.review_status = ReviewStatus.NEEDS_REVIEW
        after = _candidate_snapshot(candidate)
        if action == "REJECT":
            evidence = _effective_evidence(candidate)
            if evidence is not None:
                evidence["state"] = "REJECTED_OR_UNKNOWN_RECEIPT"
                evidence["blocking_fields"] = []
                after["evidence_first"] = evidence
        candidate.reviewed_result = after
        candidate.resolution_result = _effective_evidence(candidate)
        if candidate.resolution_result is not None:
            candidate.resolution_result["state"] = "REJECTED_OR_UNKNOWN_RECEIPT"
            candidate.resolution_result["blocking_fields"] = []
        now = datetime.now(UTC)
        candidate.updated_at = now
        await self.uow.processing.review(
            candidate, reviewer_id, action, before, after, now
        )
        await self.uow.commit()
        logger.info(
            "receipt.reviewed",
            extra={
                "candidate_id": str(candidate.id),
                "reviewer_id": str(reviewer_id),
                "action": action,
            },
        )
        return candidate

    async def purge_expired(self, before: datetime | None = None) -> int:
        count = await self.uow.processing.purge_expired(before or datetime.now(UTC))
        await self.uow.commit()
        logger.info("processing.retention_purged", extra={"count": count})
        return count

    async def resolve_exception(
        self,
        candidate_id: UUID,
        reviewer_id: UUID,
        command_id: UUID,
        expected_updated_at: str,
        field: str | None,
        action: str,
        value: str | None,
        reason: str | None = None,
    ) -> ReceiptCandidate:
        if action not in {"CONFIRM", "CORRECT", "UNAVAILABLE", "REJECT"}:
            raise Conflict("Invalid exception action.")
        if action in {"REJECT", "UNAVAILABLE"} and value is not None:
            raise Conflict("This action cannot supply a value.")
        if reason is not None and (not 1 <= len(reason.strip()) <= 500):
            raise Conflict("Invalid review reason.")
        digest = hashlib.sha256(
            json.dumps(
                {
                    "candidate_id": str(candidate_id),
                    "reviewer_id": str(reviewer_id),
                    "expected_updated_at": expected_updated_at,
                    "field": field,
                    "action": action,
                    "value": value,
                    "reason": reason,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        initial = await self.uow.processing.candidate(candidate_id)
        if initial is None:
            raise NotFound("Receipt candidate not found.")
        # Device lock also serializes command replay and canonical reference writes.
        await self.uow.processing.lock_device(initial.device_id)
        replay = await self.uow.processing.exception_command(command_id)
        if replay is not None:
            if replay.get("body_digest") != digest:
                raise Conflict("Exception command identifier was reused.")
            response = replay.get("response")
            if not isinstance(response, dict):
                raise Conflict("Invalid stored exception response.")
            await self.uow.rollback()
            return _restore_candidate(response)
        candidate = await self.uow.processing.candidate(candidate_id, for_update=True)
        if candidate is None:
            raise NotFound("Receipt candidate not found.")
        try:
            expected = datetime.fromisoformat(
                expected_updated_at.replace("Z", "+00:00")
            )
        except ValueError:
            raise Conflict("Invalid receipt revision.") from None
        if expected.tzinfo is None or expected != candidate.updated_at:
            raise Conflict("Receipt changed; refresh before resolving an exception.")
        metadata = _effective_evidence(candidate)
        if metadata is None or metadata.get("state") not in {
            "NEEDS_REVIEW",
            "REVIEW_FINALIZED",
        }:
            raise Conflict("Receipt has no active evidence exception.")
        before = _candidate_snapshot(candidate)
        machine_candidates: list[JsonValue] = []
        if action == "REJECT":
            metadata["state"] = "REJECTED_OR_UNKNOWN_RECEIPT"
            metadata["blocking_fields"] = []
            # Rejection changes workflow state, not historical trusted identity.
            # An unresolved reference remains null; no new key is promoted here.
            metadata["trusted_reference"] = reviewed_value(metadata, "reference_number")
            candidate.review_status = ReviewStatus.REJECTED
        else:
            blocked = metadata.get("blocking_fields")
            if (
                not isinstance(blocked, list)
                or field is None
                or (field not in blocked and field not in OPTIONAL_FIELDS)
            ):
                raise Conflict("Only an unresolved field may be reviewed.")
            fields = metadata.get("fields")
            if not isinstance(fields, dict):
                raise Conflict("Invalid stored field evidence.")
            current = fields.get(str(field))
            if isinstance(current, dict) and current.get("trust") in {
                FieldTrust.OPERATOR_CONFIRMED.value,
                FieldTrust.OPERATOR_CORRECTED.value,
            }:
                raise Conflict("Field has already been reviewed.")
            raw_candidates = (
                current.get("candidates") if isinstance(current, dict) else None
            )
            machine_candidates = (
                raw_candidates if isinstance(raw_candidates, list) else []
            )
            suggestion = current.get("value") if isinstance(current, dict) else None
            suggestions = {
                item.get("normalized_value")
                for item in machine_candidates
                if isinstance(item, dict)
                and isinstance(item.get("normalized_value"), str)
            }
            if isinstance(suggestion, str):
                suggestions.add(suggestion)
            if (
                action == "CONFIRM"
                and value not in suggestions
                and field not in {"classification", "layout"}
            ):
                raise Conflict("Confirm must match the suggested value exactly.")
            adapter = metadata.get("adapter")
            normalized = (
                None
                if action == "UNAVAILABLE"
                else _exception_value(
                    str(field),
                    value,
                    adapter=adapter if isinstance(adapter, str) else None,
                )
            )
            fields[str(field)] = {
                "field": field,
                "value": normalized,
                "state": "UNRESOLVED" if action == "UNAVAILABLE" else "RESOLVED",
                "trust": (
                    FieldTrust.REVIEW_REQUIRED.value
                    if action == "UNAVAILABLE"
                    else (
                        FieldTrust.OPERATOR_CONFIRMED.value
                        if action == "CONFIRM"
                        else FieldTrust.OPERATOR_CORRECTED.value
                    )
                ),
                "reason": (
                    "operator_unavailable"
                    if action == "UNAVAILABLE"
                    else (
                        "operator_confirmed"
                        if action == "CONFIRM"
                        else "operator_corrected"
                    )
                ),
                "candidates": machine_candidates,
                "operator": {
                    "reviewer_id": str(reviewer_id),
                    "command_id": str(command_id),
                    "action": action,
                    "value": normalized,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "correction": action == "CORRECT",
                    "reason": reason,
                },
            }
            if field == "classification" and action != "UNAVAILABLE":
                metadata["classification_resolved"] = True
                candidate.receipt_type = ReceiptType.INSTAPAY_TRANSFER_RECEIPT
            elif field == "layout" and action != "UNAVAILABLE":
                metadata["supported_layout"] = True
            metadata["blocking_fields"] = unresolved_required_fields(metadata)
            remaining = metadata["blocking_fields"]
            metadata["state"] = "NEEDS_REVIEW" if remaining else "REVIEW_FINALIZED"
            candidate.review_status = (
                ReviewStatus.NEEDS_REVIEW if remaining else ReviewStatus.AUTO_ACCEPTED
            )
            _apply_resolved_fields(candidate, metadata)
        if action != "REJECT":
            candidate.duplicate_of_candidate_id = None
            candidate.duplicate_reason = None
        if candidate.reference_normalized and action != "REJECT":
            duplicate = await self.uow.processing.canonical_reference(
                candidate.device_id,
                candidate.receipt_type.value,
                candidate.reference_normalized,
                exclude_candidate_id=candidate.id,
            )
            if (
                duplicate is None
                and await self.uow.processing.untrusted_reference_collision(
                    candidate.device_id,
                    candidate.receipt_type.value,
                    candidate.reference_normalized,
                    exclude_candidate_id=candidate.id,
                )
            ):
                raise Conflict(
                    "An unreviewed historical reference must be adjudicated first."
                )
            candidate.duplicate_of_candidate_id = duplicate
            candidate.duplicate_reason = "REFERENCE_NUMBER" if duplicate else None
        now = datetime.now(UTC)
        candidate.updated_at = max(
            now, candidate.updated_at + timedelta(microseconds=1)
        )
        after = _candidate_snapshot(candidate)
        after["evidence_first"] = metadata
        candidate.reviewed_result = after
        candidate.resolution_result = metadata
        await self.uow.processing.review(
            candidate,
            reviewer_id,
            "REJECT" if action == "REJECT" else "CORRECT",
            before,
            after,
            candidate.updated_at,
        )
        if action != "REJECT" and field is not None:
            provenance: dict[str, JsonValue] = {
                "object_id": str(candidate.object_id),
                "raw_event_id": str(candidate.raw_event_id),
                "machine_sources": [
                    {
                        key: item.get(key)
                        for key in (
                            "source",
                            "source_event_id",
                            "adapter",
                            "adapter_version",
                            "layout_profile",
                            "extraction_version",
                        )
                    }
                    for item in machine_candidates
                    if isinstance(item, dict)
                ],
            }
            await self.uow.processing.save_reviewed_field(
                candidate,
                reviewer_id,
                command_id,
                field,
                action,
                normalized,
                reason,
                machine_candidates,
                provenance,
                candidate.updated_at,
            )
        await self.uow.processing.save_exception_command(
            command_id,
            candidate,
            reviewer_id,
            digest,
            _serialize_candidate(candidate),
            candidate.updated_at,
        )
        await self.uow.commit()
        return candidate


def _candidate_snapshot(value: ReceiptCandidate) -> dict[str, JsonValue]:
    return {
        "amount": str(value.amount) if value.amount is not None else None,
        "currency": value.currency,
        "transaction_occurred_at": (
            value.transaction_occurred_at.isoformat()
            if value.transaction_occurred_at
            else None
        ),
        "timezone_state": value.timezone_state,
        "reference_original": value.reference_original,
        "reference_normalized": value.reference_normalized,
        "sender_name": value.sender_name,
        "recipient_name": value.recipient_name,
        "recipient_identifier": value.recipient_identifier,
        "transfer_status": value.transfer_status,
        "review_status": value.review_status.value,
        "duplicate_of_candidate_id": (
            str(value.duplicate_of_candidate_id)
            if value.duplicate_of_candidate_id
            else None
        ),
    }


def _effective_evidence(candidate: ReceiptCandidate) -> dict[str, JsonValue] | None:
    if isinstance(candidate.resolution_result, dict):
        return copy.deepcopy(candidate.resolution_result)
    return initial_resolution(
        candidate.machine_result,
        supported_receipt=candidate.receipt_type != ReceiptType.UNKNOWN_RECEIPT,
    )


def _exception_value(
    field: str, value: str | None, *, adapter: str | None = None
) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise Conflict("A bounded explicit value is required.")
    if field == "reference_number":
        if adapter == "instapay_receipt":
            from relay.application.evidence_sources import normalize_field

            normalized = normalize_field(field, value)
            if normalized is None:
                raise Conflict("Invalid explicit InstaPay reference.")
            return normalized
        normalized = normalize_reference(value)
        if (
            not 8 <= len(normalized) <= 40
            or not normalized.isascii()
            or not normalized.isalnum()
        ):
            raise Conflict("Invalid reference.")
        return normalized
    if field == "amount":
        if re.fullmatch(r"[0-9]{1,16}(?:\.[0-9]{1,2})?", value) is None:
            raise Conflict("Invalid amount.")
        amount = Decimal(value)
        if not Decimal("0") < amount <= Decimal("9999999999999999.99"):
            raise Conflict("Invalid amount.")
        return format(amount.quantize(Decimal("0.01")), "f")
    if field == "currency":
        if re.fullmatch(r"[A-Za-z]{3}", value) is None:
            raise Conflict("Invalid currency.")
        return value.upper()
    if field in {"date", "time"}:
        formats = ("%Y-%m-%d",) if field == "date" else ("%H:%M:%S", "%H:%M")
        for pattern in formats:
            try:
                parsed = datetime.strptime(value, pattern)
                return parsed.strftime("%Y-%m-%d" if field == "date" else "%H:%M:%S")
            except ValueError:
                continue
        raise Conflict("Invalid explicit date or time.")
    if field == "classification" and value == "INSTAPAY_TRANSFER_RECEIPT":
        return value
    if field == "layout" and value == "supported":
        return value
    if field in {"sender", "handle", "recipient", "recipient_identifier"}:
        if any(ord(character) < 32 for character in value):
            raise Conflict("Invalid participant value.")
        return value.strip()
    if field == "transfer_status" and value in {"SUCCESS", "FAILED", "PENDING"}:
        return value
    raise Conflict("Unsupported exception field.")


def _apply_resolved_fields(
    candidate: ReceiptCandidate, metadata: dict[str, JsonValue]
) -> None:
    fields = metadata.get("fields")
    if not isinstance(fields, dict):
        raise Conflict("Invalid field evidence.")
    values: dict[str, str] = {}
    for name in CRITICAL_FIELDS:
        trusted = reviewed_value(metadata, name)
        if trusted is not None:
            values[name] = trusted
    candidate.reference_normalized = values.get("reference_number")
    metadata["trusted_reference"] = candidate.reference_normalized
    candidate.reference_original = values.get("reference_number")
    candidate.amount = Decimal(values["amount"]) if "amount" in values else None
    candidate.currency = values.get("currency")
    candidate.transaction_occurred_at = None
    if "date" in values and "time" in values:
        candidate.transaction_occurred_at = datetime.fromisoformat(
            f"{values['date']}T{values['time']}"
        )
    candidate.sender_name = reviewed_value(metadata, "sender") or reviewed_value(
        metadata, "handle"
    )
    candidate.recipient_name = reviewed_value(metadata, "recipient")
    candidate.recipient_identifier = reviewed_value(metadata, "recipient_identifier")
    candidate.transfer_status = reviewed_value(metadata, "transfer_status")


def _serialize_candidate(candidate: ReceiptCandidate) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in asdict(candidate).items():
        if isinstance(value, (UUID, datetime, Decimal)):
            result[key] = (
                value.isoformat() if isinstance(value, datetime) else str(value)
            )
        else:
            result[key] = value
    return result


def _restore_candidate(snapshot: dict[str, JsonValue]) -> ReceiptCandidate:
    values = dict(snapshot)
    for name in (
        "id",
        "job_id",
        "object_id",
        "raw_event_id",
        "device_id",
        "duplicate_of_candidate_id",
    ):
        if values.get(name) is not None:
            values[name] = UUID(str(values[name]))  # type: ignore[assignment]
    for name in ("created_at", "updated_at", "expires_at", "transaction_occurred_at"):
        if values.get(name) is not None:
            values[name] = datetime.fromisoformat(str(values[name]))  # type: ignore[assignment]
    if values.get("amount") is not None:
        values["amount"] = Decimal(str(values["amount"]))  # type: ignore[assignment]
    values["receipt_type"] = ReceiptType(str(values["receipt_type"]))
    values["review_status"] = ReviewStatus(str(values["review_status"]))
    return ReceiptCandidate(**values)  # type: ignore[arg-type]


def _apply_corrections(
    candidate: ReceiptCandidate, values: dict[str, JsonValue]
) -> None:
    if "amount" in values:
        raw = values["amount"]
        try:
            amount = Decimal(str(raw)) if raw is not None else None
        except InvalidOperation:
            raise Conflict("Invalid corrected amount.") from None
        if amount is not None and not Decimal("0") < amount <= Decimal(
            "9999999999999999.99"
        ):
            raise Conflict("Invalid corrected amount.")
        candidate.amount = amount.quantize(Decimal("0.01")) if amount else None
    if "currency" in values:
        currency = values["currency"]
        if currency is not None and (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
        ):
            raise Conflict("Invalid corrected currency.")
        candidate.currency = currency.upper() if isinstance(currency, str) else None
    if "reference_number" in values:
        raw_reference = values["reference_number"]
        if raw_reference is not None and not isinstance(raw_reference, str):
            raise Conflict("Invalid corrected reference number.")
        candidate.reference_original = raw_reference
        candidate.reference_normalized = (
            normalize_reference(raw_reference) if raw_reference else None
        )
        if (
            candidate.reference_normalized is not None
            and not 8 <= len(candidate.reference_normalized) <= 40
        ):
            raise Conflict("Invalid corrected reference number.")
    if "transaction_occurred_at" in values:
        raw_time = values["transaction_occurred_at"]
        if raw_time is None:
            candidate.transaction_occurred_at = None
        elif isinstance(raw_time, str) and len(raw_time) <= 40:
            try:
                candidate.transaction_occurred_at = datetime.fromisoformat(raw_time)
            except ValueError:
                raise Conflict("Invalid corrected transaction time.") from None
            if (
                candidate.transaction_occurred_at.tzinfo is not None
                and candidate.transaction_occurred_at.utcoffset() is not None
            ):
                raise Conflict("Corrected receipt time must not include a timezone.")
        else:
            raise Conflict("Invalid corrected transaction time.")
    if "timezone_state" in values:
        state = values["timezone_state"]
        if state not in {"KNOWN", "UNKNOWN"}:
            raise Conflict("Invalid corrected timezone state.")
        candidate.timezone_state = str(state)
    for field in (
        "sender_name",
        "recipient_name",
        "recipient_identifier",
        "transfer_status",
    ):
        if field not in values:
            continue
        item = values[field]
        if item is not None and (
            not isinstance(item, str)
            or not 1 <= len(item) <= 160
            or any(ord(character) < 32 for character in item)
        ):
            raise Conflict(f"Invalid corrected {field}.")
        setattr(candidate, field, item)


def _validate_document(
    document: OcrDocument, job: ProcessingJob, *, allow_empty: bool = False
) -> None:
    if (
        document.provider_id != job.ocr_provider
        or document.requested_languages != job.requested_languages
        or len(document.provider_version) > 64
        or not set(document.requested_languages) <= set(document.available_languages)
    ):
        raise ProcessingError(ProcessingFailure.VALIDATION_FAILED)
    if (
        len(document.normalized_text.encode("utf-8")) > MAX_OCR_TEXT_BYTES
        or len(document.evidence_lines) > MAX_OCR_BLOCKS
    ):
        raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
    if not document.normalized_text and not allow_empty:
        raise ProcessingError(ProcessingFailure.OCR_EMPTY)
    token_count = 0
    for line in document.evidence_lines:
        if (
            len(line.text) > 512
            or (line.confidence is not None and not 0 <= line.confidence <= 1)
            or (
                line.bounding_box is not None
                and any(value < 0 for value in line.bounding_box)
            )
            or not line.ocr_mode
            or len(line.ocr_mode) > 64
            or (
                line.source_region is not None
                and any(value < 0 for value in line.source_region)
            )
            or (line.source_line_index is not None and line.source_line_index < 0)
        ):
            raise ProcessingError(ProcessingFailure.VALIDATION_FAILED)
        if line.origin == OcrLineOrigin.SECONDARY:
            if (
                line.ocr_mode != "line_crop_psm7"
                or line.source_region is None
                or line.source_line_index is None
                or line.source_line_index >= len(document.lines)
            ):
                raise ProcessingError(ProcessingFailure.VALIDATION_FAILED)
        elif line.source_region is not None or line.source_line_index is not None:
            raise ProcessingError(ProcessingFailure.VALIDATION_FAILED)
        token_count += len(line.tokens)
        if token_count > MAX_OCR_BLOCKS:
            raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
        for token in line.tokens:
            if (
                not token.text
                or len(token.text) > 512
                or (token.confidence is not None and not 0 <= token.confidence <= 1)
                or (
                    token.bounding_box is not None
                    and any(value < 0 for value in token.bounding_box)
                )
            ):
                raise ProcessingError(ProcessingFailure.VALIDATION_FAILED)
