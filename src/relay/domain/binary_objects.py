"""Collector-independent binary objects and bounded image validation."""

import hashlib
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from relay.domain.agent import JsonValue
from relay.domain.errors import DomainError

ALLOWED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_DIMENSION = 12_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_CHUNK_BYTES = 256 * 1024
HASH_PATTERN = re.compile(r"[a-f0-9]{64}", re.ASCII)


class BinaryRejected(DomainError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("Binary upload validation failed.")


@dataclass(frozen=True, slots=True)
class ImageUploadRequest:
    upload_id: UUID
    event_id: UUID
    installation_id: UUID
    content_hash: str
    content_type: str
    byte_size: int
    width: int
    height: int
    source_type: str
    source_id: str
    display_name: str | None
    config_version: int
    rule_id: str
    occurred_at: datetime
    captured_at: datetime
    media_at: datetime | None

    def fingerprint(self) -> str:
        values = (
            str(self.event_id),
            str(self.installation_id),
            self.content_hash,
            self.content_type,
            str(self.byte_size),
            str(self.width),
            str(self.height),
            self.source_type,
            self.source_id,
            str(self.config_version),
            self.rule_id,
            self.occurred_at.astimezone(UTC).isoformat(),
            self.captured_at.astimezone(UTC).isoformat(),
            self.media_at.astimezone(UTC).isoformat() if self.media_at else "",
        )
        return hashlib.sha256("\0".join(values).encode()).hexdigest()


@dataclass(slots=True)
class UploadSession:
    id: UUID
    upload_id: UUID
    event_id: UUID
    device_id: UUID
    installation_id: UUID
    request_fingerprint: str
    content_hash: str
    content_type: str
    byte_size: int
    width: int
    height: int
    source_type: str
    source_id: str
    config_version: int
    rule_id: str
    occurred_at: datetime
    captured_at: datetime
    media_at: datetime | None
    uploaded_bytes: int
    status: str
    object_id: UUID | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BinaryObject:
    id: UUID
    device_id: UUID
    installation_id: UUID
    content_hash: str
    content_type: str
    byte_size: int
    width: int
    height: int
    source_type: str
    source_id: str
    storage_backend: str
    storage_key: str
    created_at: datetime
    finalized_at: datetime
    delete_after: datetime


def validate_image_request(value: ImageUploadRequest, now: datetime) -> None:
    for stamp in (value.occurred_at, value.captured_at, value.media_at):
        if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
            raise BinaryRejected("invalid_timestamp")
    if (
        value.installation_id.int == 0
        or HASH_PATTERN.fullmatch(value.content_hash) is None
        or value.content_type not in ALLOWED_IMAGE_TYPES
        or not 1 <= value.byte_size <= MAX_IMAGE_BYTES
        or not 1 <= value.width <= MAX_IMAGE_DIMENSION
        or not 1 <= value.height <= MAX_IMAGE_DIMENSION
        or value.width * value.height > MAX_IMAGE_PIXELS
        or value.source_type != "saf_tree"
        or not 1 <= len(value.source_id) <= 64
        or not 1 <= value.config_version <= 2_147_483_647
        or not 1 <= len(value.rule_id) <= 64
    ):
        raise BinaryRejected("invalid_metadata")
    if value.display_name is not None and (
        not 1 <= len(value.display_name) <= 255
        or any(ord(character) < 32 for character in value.display_name)
    ):
        raise BinaryRejected("invalid_metadata")
    occurred = value.occurred_at.astimezone(UTC)
    captured = value.captured_at.astimezone(UTC)
    if (
        occurred < now - timedelta(days=31)
        or occurred > now + timedelta(minutes=5)
        or captured < occurred - timedelta(minutes=5)
        or captured > now + timedelta(minutes=5)
    ):
        raise BinaryRejected("invalid_timestamp")


def image_rule_matches(
    settings: dict[str, JsonValue], value: ImageUploadRequest
) -> bool:
    collectors = settings.get("collectors")
    images = collectors.get("images") if isinstance(collectors, dict) else None
    if not isinstance(images, dict) or images.get("enabled") is not True:
        return False
    rules = images.get("rules")
    if not isinstance(rules, list):
        return False
    for candidate in rules:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("id") != value.rule_id or candidate.get("enabled") is not True:
            continue
        if candidate.get("source_id") != value.source_id:
            return False
        mimes = candidate.get("mime_types")
        if isinstance(mimes, list) and value.content_type not in mimes:
            return False
        if not _within(
            value.byte_size, candidate.get("min_bytes"), candidate.get("max_bytes")
        ):
            return False
        if not _within(
            value.width, candidate.get("min_width"), candidate.get("max_width")
        ):
            return False
        if not _within(
            value.height, candidate.get("min_height"), candidate.get("max_height")
        ):
            return False
        filename = candidate.get("filename")
        if filename is not None:
            if not isinstance(filename, dict) or value.display_name is None:
                return False
            mode, needle = filename.get("mode"), filename.get("value")
            if not isinstance(needle, str) or not _text_match(
                value.display_name, needle, str(mode)
            ):
                return False
        return True
    return False


def _within(value: int, minimum: JsonValue, maximum: JsonValue) -> bool:
    return not (
        (type(minimum) is int and value < minimum)
        or (type(maximum) is int and value > maximum)
    )


def _text_match(value: str, expected: str, mode: str) -> bool:
    current, target = value.casefold(), expected.casefold()
    return {
        "contains": target in current,
        "prefix": current.startswith(target),
        "suffix": current.endswith(target),
    }.get(mode, False)


def inspect_image(data: bytes) -> tuple[str, int, int]:
    if len(data) > MAX_IMAGE_BYTES:
        raise BinaryRejected("object_too_large")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if (
            len(data) < 33
            or data[8:12] != b"\x00\x00\x00\r"
            or data[12:16] != b"IHDR"
            or data[24] not in {1, 2, 4, 8, 16}
            or data[25] not in {0, 2, 3, 4, 6}
            or data[26] != 0
            or data[27] != 0
            or data[28] not in {0, 1}
        ):
            raise BinaryRejected("malformed_image")
        width, height = struct.unpack(">II", data[16:24])
        result = ("image/png", width, height)
    elif data.startswith(b"\xff\xd8"):
        result = ("image/jpeg", *_jpeg_dimensions(data))
    elif len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        result = ("image/webp", *_webp_dimensions(data))
    else:
        raise BinaryRejected("unsupported_or_spoofed_image")
    _, width, height = result
    if (
        not 1 <= width <= MAX_IMAGE_DIMENSION
        or not 1 <= height <= MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise BinaryRejected("image_dimensions_exceeded")
    return result


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    offset = 2
    sof = frozenset(
        {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    )
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise BinaryRejected("malformed_image")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            raise BinaryRejected("malformed_image")
        if marker in sof:
            if length < 7:
                raise BinaryRejected("malformed_image")
            return (
                int.from_bytes(data[offset + 5 : offset + 7], "big"),
                int.from_bytes(data[offset + 3 : offset + 5], "big"),
            )
        offset += length
    raise BinaryRejected("malformed_image")


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    kind = data[12:16]
    if kind == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if kind == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        b1, b2, b3, b4 = data[21:25]
        return 1 + b1 + ((b2 & 0x3F) << 8), 1 + (b2 >> 6) + (b3 << 2) + (
            (b4 & 0x0F) << 10
        )
    if kind == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    raise BinaryRejected("malformed_image")
