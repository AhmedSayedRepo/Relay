"""Content-addressed filesystem store for retained training images."""

import os
import re
from pathlib import Path, PurePosixPath

from relay.domain.binary_objects import BinaryRejected

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
KEY = re.compile(r"images/[a-f0-9]{64}\.(jpg|png|webp)", re.ASCII)


class FilesystemDatasetStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        (self.root / "images").mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if KEY.fullmatch(key) is None:
            raise BinaryRejected("invalid_dataset_key")
        return self.root.joinpath(*PurePosixPath(key).parts)

    def put(self, sha256: str, content_type: str, data: bytes) -> str:
        extension = EXTENSIONS.get(content_type)
        if extension is None:
            raise BinaryRejected("unsupported_content_type")
        key = f"images/{sha256}.{extension}"
        path = self._path(key)
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        return key

    def read(self, key: str, maximum: int) -> bytes:
        try:
            with self._path(key).open("rb") as stream:
                data = stream.read(maximum + 1)
        except FileNotFoundError:
            raise BinaryRejected("dataset_content_missing") from None
        if len(data) > maximum:
            raise BinaryRejected("object_too_large")
        return data

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
