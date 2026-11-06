"""Training labels derived only from explicit operator review.

Machine suggestions never become training targets. A field is a target only when
an operator confirmed or corrected it; a field the operator marked unavailable is
kept as UNREADABLE (excluded from both value and presence loss) because
"unavailable" does not distinguish "not printed" from "illegible".
"""

import hashlib
from enum import StrEnum

from relay.domain.agent import JsonValue
from relay.domain.evidence import CRITICAL_FIELDS
from relay.domain.review_trust import REVIEWED_TRUST

LABEL_VERSION = "relay-reviewed-labels-v1"
CONTRACT_VERSION = "relay-field-value-development-v1"
HOLDOUT_EVERY = 5

# Order matches the field model contract (relay_field_v1.FIELDS).
MODEL_FIELDS = (
    "amount",
    "currency",
    "reference",
    "date",
    "time",
    "sender",
    "sender_handle",
    "recipient",
    "recipient_identifier",
    "status_evidence",
)
REVIEW_TO_MODEL = {
    "amount": "amount",
    "currency": "currency",
    "reference_number": "reference",
    "date": "date",
    "time": "time",
    "sender": "sender",
    "handle": "sender_handle",
    "recipient": "recipient",
    "recipient_identifier": "recipient_identifier",
    "transfer_status": "status_evidence",
}
ELIGIBLE_STATES = frozenset({"NEEDS_REVIEW", "REVIEW_FINALIZED"})


class Split(StrEnum):
    TRAIN = "TRAIN"
    HOLDOUT = "HOLDOUT"


class EvidenceState(StrEnum):
    PRESENT_VERIFIED = "PRESENT_VERIFIED"
    UNREADABLE = "UNREADABLE"
    UNREVIEWED = "UNREVIEWED"


def split_for_group(group_id: str) -> Split:
    """Deterministic, group-level split: every variant of a transaction lands
    in the same split, and roughly one transaction in five is held out."""
    digest = hashlib.sha256(f"relay-holdout-v1:{group_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % HOLDOUT_EVERY
    return Split.HOLDOUT if bucket == 0 else Split.TRAIN


def _field_label(item: JsonValue) -> dict[str, JsonValue]:
    state = EvidenceState.UNREVIEWED
    value: str | None = None
    if isinstance(item, dict):
        raw = item.get("value")
        if item.get("trust") in REVIEWED_TRUST and isinstance(raw, str) and raw:
            state, value = EvidenceState.PRESENT_VERIFIED, raw
        elif item.get("reason") == "operator_unavailable":
            state = EvidenceState.UNREADABLE
    present = state == EvidenceState.PRESENT_VERIFIED
    return {
        "evidence_state": state.value,
        "target_value": value,
        "value_loss_mask": int(present),
        "presence_loss_mask": int(present),
    }


def labels_from_resolution(
    metadata: JsonValue,
) -> dict[str, dict[str, JsonValue]] | None:
    """Return model-ordered field labels, or None when the receipt is not a
    fully adjudicated, supported transfer receipt."""
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 2:
        return None
    if metadata.get("state") not in ELIGIBLE_STATES:
        return None
    if "auto_confirmed" in metadata:  # never train on the model's own output
        return None
    if metadata.get("classification_resolved") is False:
        return None
    if metadata.get("supported_layout") is False:
        return None
    fields = metadata.get("fields")
    if not isinstance(fields, dict):
        return None
    by_review = {name: _field_label(fields.get(name)) for name in REVIEW_TO_MODEL}
    critical = [by_review[name]["evidence_state"] for name in CRITICAL_FIELDS]
    if any(state == EvidenceState.UNREVIEWED.value for state in critical):
        return None
    if EvidenceState.PRESENT_VERIFIED.value not in critical:
        return None
    labels = {REVIEW_TO_MODEL[name]: label for name, label in by_review.items()}
    return {name: labels[name] for name in MODEL_FIELDS}
