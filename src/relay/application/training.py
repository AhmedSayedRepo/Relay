"""Capture operator-reviewed receipts as a consented, self-contained dataset.

Receipt images normally expire. When a device has opted in, a fully reviewed
receipt is copied into separate dataset storage with labels taken only from
operator decisions. Copies are kept until an operator deletes them; erasing a
device's training data removes both rows and unreferenced images.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from relay.application.errors import NotFound
from relay.application.training_ports import (
    DatasetStore,
    SourceImageReader,
    TrainingExample,
    TrainingUnitOfWork,
)
from relay.domain.agent import JsonValue
from relay.domain.binary_objects import MAX_IMAGE_BYTES, BinaryRejected
from relay.domain.training import (
    CONTRACT_VERSION,
    LABEL_VERSION,
    Split,
    labels_from_resolution,
    split_for_group,
)

logger = logging.getLogger(__name__)
SCAN_FACTOR = 5


class TrainingDatasetService:
    def __init__(
        self,
        uow: TrainingUnitOfWork,
        source: SourceImageReader,
        store: DatasetStore,
    ) -> None:
        self.uow = uow
        self.source = source
        self.store = store

    async def consent(self, device_id: UUID) -> bool:
        if not await self.uow.training.device_exists(device_id):
            raise NotFound("Device not found.")
        return await self.uow.training.consent(device_id)

    async def set_consent(self, device_id: UUID, enabled: bool, actor_id: UUID) -> bool:
        if not await self.uow.training.device_exists(device_id):
            raise NotFound("Device not found.")
        await self.uow.training.set_consent(
            device_id, enabled, actor_id, datetime.now(UTC)
        )
        await self.uow.commit()
        return enabled

    async def capture(self, *, limit: int = 100) -> int:
        """Copy newly eligible reviewed receipts; idempotent and bounded."""
        captured = 0
        sources = await self.uow.training.capture_sources(
            scan_limit=limit * SCAN_FACTOR
        )
        for source in sources:
            if captured >= limit:
                break
            labels = labels_from_resolution(source.resolution)
            if labels is None:
                continue
            try:
                data = self.source.read(source.storage_key, MAX_IMAGE_BYTES)
            except BinaryRejected:  # purged or unreadable source image
                logger.info("training.capture.source_unavailable")
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest != source.content_hash:
                logger.warning("training.capture.hash_mismatch")
                continue
            key = self.store.put(digest, source.content_type, data)
            payload: dict[str, JsonValue] = {k: v for k, v in labels.items()}
            await self.uow.training.add_example(
                TrainingExample(
                    id=uuid4(),
                    candidate_id=source.candidate_id,
                    device_id=source.device_id,
                    transaction_group_id=source.transaction_group_id,
                    split=split_for_group(source.transaction_group_id).value,
                    image_key=key,
                    image_sha256=digest,
                    content_type=source.content_type,
                    labels=payload,
                    label_version=LABEL_VERSION,
                    created_at=datetime.now(UTC),
                )
            )
            captured += 1
        await self.uow.commit()
        return captured

    async def summary(self) -> dict[str, int]:
        return await self.uow.training.summary()

    async def delete_device(self, device_id: UUID) -> int:
        deleted, orphaned = await self.uow.training.delete_device(device_id)
        await self.uow.commit()
        for key in orphaned:
            self.store.delete(key)
        return deleted

    async def export(
        self, destination: Path, *, include_holdout: bool
    ) -> dict[str, int]:
        """Write images plus JSONL manifests in the field-model record format.

        The holdout split is written only on explicit request and to its own
        manifest, so it cannot leak into training by accident."""
        destination.mkdir(parents=True, exist_ok=True)
        images = destination / "images"
        images.mkdir(exist_ok=True)
        counts = {Split.TRAIN.value: 0, Split.HOLDOUT.value: 0}
        manifests = {
            Split.TRAIN.value: destination / "train-manifest.jsonl",
            Split.HOLDOUT.value: destination / "holdout-manifest.jsonl",
        }
        handles = {
            split: path.open("w", encoding="utf-8")
            for split, path in manifests.items()
            if split == Split.TRAIN.value or include_holdout
        }
        try:
            for example in await self.uow.training.examples():
                handle = handles.get(example.split)
                if handle is None:
                    continue
                name = Path(example.image_key).name
                target = images / name
                if not target.exists():
                    target.write_bytes(
                        self.store.read(example.image_key, MAX_IMAGE_BYTES)
                    )
                record: dict[str, JsonValue] = {
                    "contract_version": CONTRACT_VERSION,
                    "label_version": example.label_version,
                    "sample_id": str(example.id),
                    "transaction_group_id": example.transaction_group_id,
                    "source_kind": "relay_reviewed_real",
                    "split": example.split,
                    "image_path": f"images/{name}",
                    "image_sha256": example.image_sha256,
                    "fields": example.labels,
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                counts[example.split] += 1
        finally:
            for handle in handles.values():
                handle.close()
        return counts
