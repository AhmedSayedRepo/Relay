"""Installation identity and opaque configuration; no collection semantics."""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from relay.domain.errors import InvalidDevice

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)

RULE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)


def _bounded_terms(value: JsonValue) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 10
        and all(isinstance(item, str) and 1 <= len(item) <= 128 for item in value)
    )


def _validate_sms_configuration(collectors: dict[str, JsonValue]) -> None:
    sms = collectors.get("sms")
    if sms is None:
        return
    if not isinstance(sms, dict) or set(sms) != {"enabled", "rules"}:
        raise InvalidDevice("Invalid SMS collector configuration.")
    rules = sms.get("rules")
    if (
        type(sms.get("enabled")) is not bool
        or not isinstance(rules, list)
        or len(rules) > 20
    ):
        raise InvalidDevice("Invalid SMS collector configuration.")
    identifiers: set[str] = set()
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or not {"id", "enabled"} <= set(rule)
            or not set(rule) <= {"id", "enabled", "sender", "body"}
        ):
            raise InvalidDevice("Invalid SMS rule.")
        identifier = rule.get("id")
        if (
            not isinstance(identifier, str)
            or RULE_ID.fullmatch(identifier) is None
            or identifier in identifiers
            or type(rule.get("enabled")) is not bool
            or ("sender" not in rule and "body" not in rule)
        ):
            raise InvalidDevice("Invalid SMS rule.")
        identifiers.add(identifier)
        sender = rule.get("sender")
        sender_value = sender.get("value") if isinstance(sender, dict) else None
        if sender is not None and (
            not isinstance(sender, dict)
            or set(sender) != {"mode", "value"}
            or sender.get("mode") not in {"exact", "contains", "prefix", "suffix"}
            or not isinstance(sender_value, str)
            or not 1 <= len(sender_value) <= 128
        ):
            raise InvalidDevice("Invalid SMS sender rule.")
        body = rule.get("body")
        if body is not None:
            if (
                not isinstance(body, dict)
                or not body
                or not set(body) <= {"contains", "any_contains", "all_contains"}
            ):
                raise InvalidDevice("Invalid SMS body rule.")
            contains = body.get("contains")
            if contains is not None and (
                not isinstance(contains, str) or not 1 <= len(contains) <= 128
            ):
                raise InvalidDevice("Invalid SMS body rule.")
            for key in ("any_contains", "all_contains"):
                if key in body and not _bounded_terms(body[key]):
                    raise InvalidDevice("Invalid SMS body rule.")


def _validate_image_configuration(collectors: dict[str, JsonValue]) -> None:
    images = collectors.get("images")
    if images is None:
        return
    if not isinstance(images, dict) or set(images) != {"enabled", "sources", "rules"}:
        raise InvalidDevice("Invalid image collector configuration.")
    sources, rules = images.get("sources"), images.get("rules")
    if (
        type(images.get("enabled")) is not bool
        or not isinstance(sources, list)
        or not 1 <= len(sources) <= 5
        or not isinstance(rules, list)
        or len(rules) > 10
    ):
        raise InvalidDevice("Invalid image collector configuration.")
    source_ids: set[str] = set()
    for source in sources:
        source_identifier = source.get("id") if isinstance(source, dict) else None
        if (
            not isinstance(source, dict)
            or set(source) != {"id", "type"}
            or source.get("type") != "saf_tree"
            or not isinstance(source_identifier, str)
            or RULE_ID.fullmatch(source_identifier) is None
            or source_identifier in source_ids
        ):
            raise InvalidDevice("Invalid image source.")
        source_ids.add(source_identifier)
    rule_ids: set[str] = set()
    allowed = {
        "id",
        "enabled",
        "source_id",
        "mime_types",
        "filename",
        "min_bytes",
        "max_bytes",
        "min_width",
        "max_width",
        "min_height",
        "max_height",
    }
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or not {"id", "enabled", "source_id"} <= set(rule)
            or not set(rule) <= allowed
        ):
            raise InvalidDevice("Invalid image rule.")
        identifier, source_id = rule.get("id"), rule.get("source_id")
        if (
            not isinstance(identifier, str)
            or RULE_ID.fullmatch(identifier) is None
            or identifier in rule_ids
            or type(rule.get("enabled")) is not bool
            or source_id not in source_ids
        ):
            raise InvalidDevice("Invalid image rule.")
        rule_ids.add(identifier)
        mimes = rule.get("mime_types")
        if mimes is not None and (
            not isinstance(mimes, list)
            or not 1 <= len(mimes) <= 3
            or len(set(str(item) for item in mimes)) != len(mimes)
            or any(
                item not in {"image/jpeg", "image/png", "image/webp"} for item in mimes
            )
        ):
            raise InvalidDevice("Invalid image MIME filter.")
        filename = rule.get("filename")
        filename_value = filename.get("value") if isinstance(filename, dict) else None
        if filename is not None and (
            not isinstance(filename, dict)
            or set(filename) != {"mode", "value"}
            or filename.get("mode") not in {"contains", "prefix", "suffix"}
            or not isinstance(filename_value, str)
            or not 1 <= len(filename_value) <= 128
        ):
            raise InvalidDevice("Invalid image filename filter.")
        bounds = {
            "min_bytes": (1, 8 * 1024 * 1024),
            "max_bytes": (1, 8 * 1024 * 1024),
            "min_width": (1, 12_000),
            "max_width": (1, 12_000),
            "min_height": (1, 12_000),
            "max_height": (1, 12_000),
        }
        for key, (minimum, maximum) in bounds.items():
            value = rule.get(key)
            if value is not None and (
                type(value) is not int or not minimum <= value <= maximum
            ):
                raise InvalidDevice("Invalid image bounds.")
        for minimum_key, maximum_key in (
            ("min_bytes", "max_bytes"),
            ("min_width", "max_width"),
            ("min_height", "max_height"),
        ):
            minimum_value = rule.get(minimum_key)
            maximum_value = rule.get(maximum_key)
            if (
                isinstance(minimum_value, int)
                and not isinstance(minimum_value, bool)
                and isinstance(maximum_value, int)
                and not isinstance(maximum_value, bool)
                and minimum_value > maximum_value
            ):
                raise InvalidDevice("Invalid image bounds.")


def _validate_collectors(settings: dict[str, JsonValue]) -> None:
    collectors = settings.get("collectors")
    if collectors is None:
        return
    if not isinstance(collectors, dict) or not set(collectors) <= {"sms", "images"}:
        raise InvalidDevice("Invalid collector configuration.")
    _validate_sms_configuration(collectors)
    _validate_image_configuration(collectors)


def validate_settings(settings: dict[str, JsonValue]) -> None:
    def visit(value: JsonValue, depth: int) -> None:
        if depth > 8:
            raise InvalidDevice("Configuration exceeds nesting limits.")
        if isinstance(value, dict):
            for key, item in value.items():
                if not key or len(key) > 128 or any(ord(c) < 32 for c in key):
                    raise InvalidDevice("Invalid configuration key.")
                visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
        elif isinstance(value, float) and not math.isfinite(value):
            raise InvalidDevice("Configuration numbers must be finite.")

    visit(settings, 0)
    if len(json.dumps(settings, ensure_ascii=False).encode("utf-8")) > 8192:
        raise InvalidDevice("Configuration exceeds size limits.")
    interval = settings.get("sync_interval_seconds")
    if "sync_interval_seconds" in settings and (
        type(interval) is not int or not 900 <= interval <= 86400
    ):
        raise InvalidDevice("Invalid synchronization interval.")
    _validate_collectors(settings)


@dataclass(slots=True)
class Challenge:
    id: UUID
    device_id: UUID
    secret_hash: str = field(repr=False)
    expires_at: datetime
    replace_existing: bool
    attempts: int = 0
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(slots=True)
class Installation:
    id: UUID
    device_id: UUID
    enrolled_at: datetime
    revoked_at: datetime | None = None
    acknowledged_version: int | None = None
    reported_config_version: int = 0


@dataclass(slots=True)
class Credential:
    id: UUID
    installation_id: UUID
    device_id: UUID
    secret_hash: str = field(repr=False)
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    consumed_request_id: UUID | None = None
    successor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Configuration:
    device_id: UUID
    version: int
    schema_version: int
    settings: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class DevicePrincipal:
    device_id: UUID
    installation_id: UUID
    credential_id: UUID
