"""Filesystem object storage for local development and single-node deployments."""

import os
from pathlib import Path, PurePosixPath
from uuid import UUID

from relay.domain.binary_objects import BinaryRejected


class FilesystemObjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.staging = self.root / "staging"
        self.objects = self.root / "objects"
        self.staging.mkdir(parents=True, exist_ok=True)
        self.objects.mkdir(parents=True, exist_ok=True)

    def _upload_path(self, session_id: UUID) -> Path:
        return self.staging / f"{session_id}.upload"

    def _object_path(self, storage_key: str) -> Path:
        value = PurePosixPath(storage_key)
        if (
            value.is_absolute()
            or ".." in value.parts
            or value.parts[:1] != ("objects",)
        ):
            raise BinaryRejected("invalid_storage_key")
        path = self.root.joinpath(*value.parts).resolve()
        if self.root not in path.parents:
            raise BinaryRejected("invalid_storage_key")
        return path

    def create_upload(self, session_id: UUID) -> None:
        try:
            self._upload_path(session_id).touch(exist_ok=False)
        except FileExistsError:
            return

    def append(self, session_id: UUID, offset: int, data: bytes) -> None:
        path = self._upload_path(session_id)
        try:
            with path.open("r+b") as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell() != offset:
                    raise BinaryRejected("bad_offset")
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileNotFoundError:
            raise BinaryRejected("upload_state_missing") from None

    def matches(self, session_id: UUID, offset: int, data: bytes) -> bool:
        try:
            with self._upload_path(session_id).open("rb") as stream:
                stream.seek(offset)
                return stream.read(len(data)) == data
        except FileNotFoundError:
            raise BinaryRejected("upload_state_missing") from None

    def read_upload(self, session_id: UUID, maximum: int) -> bytes:
        try:
            with self._upload_path(session_id).open("rb") as stream:
                data = stream.read(maximum + 1)
        except FileNotFoundError:
            raise BinaryRejected("upload_state_missing") from None
        if len(data) > maximum:
            raise BinaryRejected("object_too_large")
        return data

    def finalize(self, session_id: UUID, storage_key: str) -> None:
        source = self._upload_path(session_id)
        destination = self._object_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, destination)
        except FileNotFoundError:
            raise BinaryRejected("upload_state_missing") from None

    def delete_upload(self, session_id: UUID) -> None:
        self._upload_path(session_id).unlink(missing_ok=True)

    def read(self, storage_key: str, maximum: int) -> bytes:
        try:
            with self._object_path(storage_key).open("rb") as stream:
                data = stream.read(maximum + 1)
        except FileNotFoundError:
            raise BinaryRejected("object_content_missing") from None
        if len(data) > maximum:
            raise BinaryRejected("object_too_large")
        return data

    def delete(self, storage_key: str) -> None:
        self._object_path(storage_key).unlink(missing_ok=True)
