"""Resolved evidence -> existing generic receipt persistence contract."""

from datetime import datetime
from decimal import Decimal

from relay.application.evidence_ports import ReceiptEvidence
from relay.domain.evidence import FieldCandidate, ProductState, ResolutionPolicy
from relay.domain.processing import (
    ExtractionField,
    FieldStatus,
    ParsedReceipt,
    ReviewStatus,
)


def resolve_evidence(
    evidence: ReceiptEvidence,
    structured_candidates: tuple[FieldCandidate, ...] = (),
) -> ParsedReceipt:
    transaction = ResolutionPolicy().resolve(
        (*evidence.candidates, *structured_candidates),
        classification_resolved=evidence.classification_resolved,
        supported_layout=evidence.supported_layout,
        receipt_possible=evidence.receipt_possible,
        explicit_transfer_failure=evidence.explicit_transfer_failure,
    )
    # Adapter provenance must not overwrite the generic trust decision.
    metadata = {**evidence.metadata, **transaction.as_json()}
    # Candidates remain immutable machine evidence; these columns hold only
    # resolved data. In particular an untrusted reference cannot reserve a key.
    values = {
        field.field: field.value for field in transaction.fields if field.resolved
    }
    occurred = None
    if values.get("date") and values.get("time"):
        occurred = datetime.fromisoformat(f"{values['date']}T{values['time']}:00")
    fields = tuple(
        ExtractionField(
            field.field,
            field.value,
            field.candidates[0].original_text if field.candidates else None,
            max(
                (candidate.confidence or 0 for candidate in field.candidates), default=0
            ),
            (
                FieldStatus.CONFIDENT
                if field.resolved
                else (
                    FieldStatus.UNCERTAIN if field.candidates else FieldStatus.MISSING
                )
            ),
            (field.reason,),
        )
        for field in transaction.fields
    )
    review = (
        ReviewStatus.AUTO_ACCEPTED
        if transaction.state == ProductState.AUTO_FINALIZED
        else ReviewStatus.NEEDS_REVIEW
    )
    if transaction.state in {ProductState.UNKNOWN, ProductState.TRANSFER_FAILED}:
        review = ReviewStatus.REJECTED
    return ParsedReceipt(
        receipt_type=evidence.receipt_type,
        parser_version="v1",
        classification_evidence=("receipt_adapter",),
        fields=fields,
        review_status=review,
        amount=Decimal(str(values["amount"])) if values.get("amount") else None,
        currency=values.get("currency"),
        transaction_occurred_at=occurred,
        timezone_state="UNKNOWN",
        reference_original=values.get("reference_number"),
        reference_normalized=transaction.trusted_reference,
        sender_name=values.get("sender") or values.get("handle"),
        recipient_name=values.get("recipient"),
        recipient_identifier=values.get("recipient_identifier"),
        transfer_status="FAILED" if evidence.explicit_transfer_failure else None,
        evidence_result=metadata,
    )
