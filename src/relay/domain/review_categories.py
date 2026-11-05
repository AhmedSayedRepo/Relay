"""Group receipts waiting for review by the work they need."""

from collections import Counter
from collections.abc import Iterable

from relay.domain.agent import JsonValue
from relay.domain.evidence import CRITICAL_FIELDS

CATEGORIES = ("ready", "missing", "duplicate", "in_progress", "done")
OPERATOR_TRUST = {"OPERATOR_CONFIRMED", "OPERATOR_CORRECTED"}


def _fields(row: dict[str, JsonValue]) -> dict[str, JsonValue]:
    resolution = row.get("resolution_result")
    fields = resolution.get("fields") if isinstance(resolution, dict) else None
    return fields if isinstance(fields, dict) else {}


def _blocking(row: dict[str, JsonValue]) -> list[str]:
    resolution = row.get("resolution_result")
    blocking = (
        resolution.get("blocking_fields") if isinstance(resolution, dict) else None
    )
    return (
        [item for item in blocking if isinstance(item, str)]
        if isinstance(blocking, list)
        else []
    )


def suggestion(field: JsonValue) -> str | None:
    """The one value Review would offer to Confirm, if there is exactly one."""
    if not isinstance(field, dict):
        return None
    candidates = field.get("candidates")
    values = {
        str(item.get("normalized_value") or item.get("value"))
        for item in (candidates if isinstance(candidates, list) else [])
        if isinstance(item, dict)
        and (item.get("normalized_value") or item.get("value"))
    }
    value = field.get("value")
    if isinstance(value, str) and value:
        values.add(value)
    return values.pop() if len(values) == 1 else None


def _handled(fields: dict[str, JsonValue], name: str) -> bool:
    """An operator decided the field: confirmed, corrected or unavailable."""
    field = fields.get(name)
    return isinstance(field, dict) and (
        field.get("trust") in OPERATOR_TRUST
        or field.get("reason") == "operator_unavailable"
    )


def categorize(rows: Iterable[dict[str, JsonValue]]) -> dict[str, str]:
    """Map receipt id to one category, checked in this order:
    done (every remaining field was marked unavailable), in_progress (an
    operator already decided a field), duplicate (another waiting receipt
    suggests the same reference), missing (a field to decide has no
    suggestion), ready (every field to decide has one suggestion)."""
    rows = list(rows)
    references = {
        str(row["id"]): suggestion(_fields(row).get("reference_number")) for row in rows
    }
    counts = Counter(value for value in references.values() if value)
    result: dict[str, str] = {}
    for row in rows:
        identifier = str(row["id"])
        fields = _fields(row)

        decided = any(_handled(fields, name) for name in CRITICAL_FIELDS)
        reference = references[identifier]
        if all(_handled(fields, name) for name in _blocking(row)):
            # Everything left was marked unavailable: nothing more to decide.
            result[identifier] = "done"
        elif decided:
            result[identifier] = "in_progress"
        elif reference and counts[reference] > 1:
            result[identifier] = "duplicate"
        elif any(suggestion(fields.get(name)) is None for name in _blocking(row)):
            result[identifier] = "missing"
        else:
            result[identifier] = "ready"
    return result
