"""Generic raw-event rules; collector payloads remain explicitly versioned."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from relay.domain.agent import JsonValue

MAX_BATCH_EVENTS = 20
MAX_EVENT_BYTES = 8192
MAX_SMS_BODY_BYTES = 4096
RULE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)


class EventRejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class EventSubmission:
    event_id: UUID
    installation_id: UUID
    collector: str
    schema_version: int
    config_version: int
    occurred_at: datetime
    captured_at: datetime
    sender: str
    body: str
    rule_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class RawEvent:
    id: UUID
    event_id: UUID
    device_id: UUID
    installation_id: UUID
    collector: str
    schema_version: int
    config_version: int
    rule_id: str
    occurred_at: datetime
    captured_at: datetime
    received_at: datetime
    payload: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    content_hash: str
    delete_after: datetime
    object_id: UUID | None = None


def _timestamp(value: JsonValue) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise EventRejected("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EventRejected("invalid_timestamp") from None
    if parsed.tzinfo is None:
        raise EventRejected("invalid_timestamp")
    return parsed.astimezone(UTC)


def parse_submission(raw: JsonValue, now: datetime) -> EventSubmission:
    expected = {
        "event_id",
        "installation_id",
        "collector",
        "schema_version",
        "config_version",
        "occurred_at",
        "captured_at",
        "payload",
        "metadata",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise EventRejected("invalid_envelope")
    try:
        encoded = json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        event_id = UUID(str(raw["event_id"]))
        installation_id = UUID(str(raw["installation_id"]))
    except (TypeError, ValueError, KeyError):
        raise EventRejected("invalid_envelope") from None
    if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
        raise EventRejected("payload_too_large")
    if raw["collector"] != "sms":
        raise EventRejected("unsupported_collector")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise EventRejected("unsupported_schema")
    if (
        type(raw["config_version"]) is not int
        or not 1 <= raw["config_version"] <= 2_147_483_647
    ):
        raise EventRejected("invalid_config_version")
    occurred = _timestamp(raw["occurred_at"])
    captured = _timestamp(raw["captured_at"])
    if occurred > now + timedelta(minutes=5) or occurred < now - timedelta(days=31):
        raise EventRejected("invalid_timestamp")
    if captured < occurred - timedelta(minutes=5) or captured > now + timedelta(
        minutes=5
    ):
        raise EventRejected("invalid_timestamp")
    payload = raw["payload"]
    metadata = raw["metadata"]
    if not isinstance(payload, dict) or set(payload) != {"sender", "body"}:
        raise EventRejected("invalid_payload")
    if not isinstance(metadata, dict) or set(metadata) != {"rule_id"}:
        raise EventRejected("invalid_metadata")
    sender, body, rule_id = payload["sender"], payload["body"], metadata["rule_id"]
    if (
        not isinstance(sender, str)
        or not 1 <= len(sender) <= 160
        or sender != sender.strip()
        or any(ord(character) < 32 for character in sender)
    ):
        raise EventRejected("invalid_payload")
    if (
        not isinstance(body, str)
        or not body
        or len(body.encode("utf-8")) > MAX_SMS_BODY_BYTES
        or any(ord(character) < 32 and character not in "\t\n\r" for character in body)
    ):
        raise EventRejected("invalid_payload")
    if not isinstance(rule_id, str) or RULE_ID.fullmatch(rule_id) is None:
        raise EventRejected("invalid_metadata")
    return EventSubmission(
        event_id,
        installation_id,
        "sms",
        1,
        int(raw["config_version"]),
        occurred,
        captured,
        sender,
        body,
        rule_id,
        hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def sms_rule_matches(
    settings: dict[str, JsonValue], rule_id: str, sender: str, body: str
) -> bool:
    collectors = settings.get("collectors")
    if not isinstance(collectors, dict):
        return False
    sms = collectors.get("sms")
    if not isinstance(sms, dict) or sms.get("enabled") is not True:
        return False
    rules = sms.get("rules")
    if not isinstance(rules, list):
        return False
    for value in rules:
        if (
            not isinstance(value, dict)
            or value.get("id") != rule_id
            or value.get("enabled") is not True
        ):
            continue
        sender_rule = value.get("sender")
        body_rule = value.get("body")
        if _sender_matches(sender_rule, sender) and _body_matches(body_rule, body):
            return True
    return False


def _sender_matches(value: JsonValue, sender: str) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    mode, needle = value.get("mode"), value.get("value")
    if not isinstance(needle, str):
        return False
    candidate, expected = sender.casefold(), needle.casefold()
    return {
        "exact": candidate == expected,
        "contains": expected in candidate,
        "prefix": candidate.startswith(expected),
        "suffix": candidate.endswith(expected),
    }.get(str(mode), False)


def _body_matches(value: JsonValue, body: str) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    candidate = body.casefold()
    contains = value.get("contains")
    any_contains = value.get("any_contains", [])
    all_contains = value.get("all_contains", [])
    if contains is not None and (
        not isinstance(contains, str) or contains.casefold() not in candidate
    ):
        return False
    if not isinstance(any_contains, list) or not isinstance(all_contains, list):
        return False
    if any_contains and not any(
        isinstance(item, str) and item.casefold() in candidate for item in any_contains
    ):
        return False
    return all(
        isinstance(item, str) and item.casefold() in candidate for item in all_contains
    )
