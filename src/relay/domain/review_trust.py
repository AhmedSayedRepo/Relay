"""Provider-neutral trust boundary for receipt suggestions and reviewed fields.

Machine recognition can propose a value, but Sprint 8 never turns that proposal
into authoritative transaction data without an explicit operator action.
"""

import copy
from enum import StrEnum

from relay.domain.agent import JsonValue
from relay.domain.evidence import CRITICAL_FIELDS


class FieldTrust(StrEnum):
    MACHINE_ONLY = "MACHINE_ONLY"
    UNRESOLVED = "UNRESOLVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    OPERATOR_CONFIRMED = "OPERATOR_CONFIRMED"
    OPERATOR_CORRECTED = "OPERATOR_CORRECTED"
    TRUSTED_AUTOMATIC = "TRUSTED_AUTOMATIC"


REVIEWED_TRUST = frozenset(
    {FieldTrust.OPERATOR_CONFIRMED.value, FieldTrust.OPERATOR_CORRECTED.value}
)
# A unique bank SMS already proves the payment's amount, date and time, so
# only the amount and currency must be settled; reference, date and time
# become optional for an SMS-verified receipt.
SMS_REQUIRED_FIELDS = ("amount", "currency")
OPTIONAL_FIELDS = (
    "sender",
    "handle",
    "recipient",
    "recipient_identifier",
    "transfer_status",
)


def initial_resolution(
    machine_result: dict[str, JsonValue], *, supported_receipt: bool
) -> dict[str, JsonValue]:
    """Build a *separate* review projection without altering machine evidence."""
    raw = machine_result.get("evidence_first")
    metadata: dict[str, JsonValue] = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    raw_fields = metadata.get("fields")
    fields: dict[str, JsonValue] = (
        copy.deepcopy(raw_fields) if isinstance(raw_fields, dict) else {}
    )
    if not raw_fields:
        extracted = machine_result.get("fields")
        if isinstance(extracted, list):
            for item in extracted:
                if not isinstance(item, dict) or not isinstance(item.get("field"), str):
                    continue
                name = str(item["field"])
                value = item.get("value")
                candidates: list[JsonValue] = []
                if isinstance(value, str) and value:
                    candidates.append(
                        {
                            "field": name,
                            "value": value,
                            "normalized_value": value,
                            "original_text": item.get("original_text") or value,
                            "source": "receipt",
                            "adapter": machine_result.get("parser_version"),
                            "adapter_version": machine_result.get("parser_version"),
                        }
                    )
                fields[name] = {"field": name, "value": value, "candidates": candidates}
    for name in (*CRITICAL_FIELDS, *OPTIONAL_FIELDS):
        field = fields.get(name)
        current: dict[str, JsonValue] = field if isinstance(field, dict) else {}
        suggestion = current.get("value")
        current.setdefault("value", None)
        current.update(
            {
                "field": name,
                "state": "UNRESOLVED",
                "trust": (
                    FieldTrust.MACHINE_ONLY.value
                    if isinstance(suggestion, str) and suggestion
                    else FieldTrust.REVIEW_REQUIRED.value
                ),
                "reason": "operator_review_required",
                "candidates": (
                    current.get("candidates")
                    if isinstance(current.get("candidates"), list)
                    else []
                ),
            }
        )
        fields[name] = current
    for name, extra in fields.items():
        # Legacy parser extras (e.g. transaction_date) are kept for audit but
        # shaped like any field, so clients can read the record.
        if name not in (*CRITICAL_FIELDS, *OPTIONAL_FIELDS) and isinstance(extra, dict):
            extra.setdefault("field", name)
            extra.setdefault("state", "UNRESOLVED")
            extra.setdefault("reason", "legacy_parser_field")
            if not isinstance(extra.get("candidates"), list):
                extra["candidates"] = []
    transfer_failed = metadata.get("state") == "TRANSFER_FAILED"
    blocking: list[JsonValue] = (
        list(CRITICAL_FIELDS) if supported_receipt and not transfer_failed else []
    )
    if supported_receipt and not transfer_failed:
        if metadata.get("classification_resolved") is False:
            blocking.insert(0, "classification")
        if metadata.get("supported_layout") is False:
            blocking.insert(0, "layout")
    metadata.update(
        {
            "schema_version": 2,
            "state": (
                "TRANSFER_FAILED"
                if transfer_failed
                else (
                    "NEEDS_REVIEW"
                    if supported_receipt
                    else "REJECTED_OR_UNKNOWN_RECEIPT"
                )
            ),
            "blocking_fields": blocking,
            "fields": fields,
            "trusted_reference": None,
            "payment_verification": "NOT_PERFORMED",
            "authenticity": "NOT_ASSESSED",
            "auto_trust_enabled": False,
        }
    )
    return metadata


def reviewed_value(metadata: dict[str, JsonValue] | None, field: str) -> str | None:
    """Return only an explicitly reviewed field; ignore machine-supplied trust."""
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 2:
        return None
    fields = metadata.get("fields")
    item = fields.get(field) if isinstance(fields, dict) else None
    if not isinstance(item, dict) or item.get("trust") not in REVIEWED_TRUST:
        return None
    value = item.get("value")
    return value if isinstance(value, str) and value else None


def unresolved_required_fields(metadata: dict[str, JsonValue]) -> list[JsonValue]:
    """Derive blockers from trust, never from a mutable cached blocker list."""
    blocked: list[JsonValue] = []
    if metadata.get("classification_resolved") is False:
        blocked.append("classification")
    if metadata.get("supported_layout") is False:
        blocked.append("layout")
    required = SMS_REQUIRED_FIELDS if metadata.get("sms_verified") else CRITICAL_FIELDS
    blocked.extend(name for name in required if reviewed_value(metadata, name) is None)
    return blocked
