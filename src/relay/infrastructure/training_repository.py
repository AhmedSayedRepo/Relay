from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.training_ports import CaptureSource, TrainingExample
from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.models import DeviceRow
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.training_models import TrainingConsentRow, TrainingExampleRow


def to_example(row: TrainingExampleRow) -> TrainingExample:
    return TrainingExample(
        id=row.id,
        candidate_id=row.candidate_id,
        device_id=row.device_id,
        transaction_group_id=row.transaction_group_id,
        split=row.split,
        image_key=row.image_key,
        image_sha256=row.image_sha256,
        content_type=row.content_type,
        labels=dict(row.labels),
        label_version=row.label_version,
        created_at=aware(row.created_at),
    )


class SqlTrainingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def device_exists(self, device_id: UUID) -> bool:
        return await self.session.get(DeviceRow, device_id) is not None

    async def consent(self, device_id: UUID) -> bool:
        row = await self.session.get(TrainingConsentRow, device_id)
        return bool(row and row.enabled)

    async def set_consent(
        self, device_id: UUID, enabled: bool, actor_id: UUID, now: datetime
    ) -> None:
        row = await self.session.get(TrainingConsentRow, device_id)
        if row is None:
            self.session.add(
                TrainingConsentRow(
                    device_id=device_id,
                    enabled=enabled,
                    updated_by=actor_id,
                    updated_at=now,
                )
            )
        else:
            row.enabled = enabled
            row.updated_by = actor_id
            row.updated_at = now
        await self.session.flush()

    async def capture_sources(self, *, scan_limit: int) -> list[CaptureSource]:
        query = (
            select(ReceiptCandidateRow, BinaryObjectRow)
            .join(BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id)
            .join(
                TrainingConsentRow,
                TrainingConsentRow.device_id == ReceiptCandidateRow.device_id,
            )
            .outerjoin(
                TrainingExampleRow,
                TrainingExampleRow.candidate_id == ReceiptCandidateRow.id,
            )
            .where(
                TrainingConsentRow.enabled.is_(True),
                TrainingExampleRow.id.is_(None),
                ReceiptCandidateRow.resolution_result.is_not(None),
                ReceiptCandidateRow.review_status != "REJECTED",
                ReceiptCandidateRow.receipt_type == "INSTAPAY_TRANSFER_RECEIPT",
            )
            .order_by(ReceiptCandidateRow.updated_at, ReceiptCandidateRow.id)
            .limit(scan_limit)
        )
        result = await self.session.execute(query)
        sources: list[CaptureSource] = []
        for candidate, binary in result.tuples():
            group = candidate.duplicate_of_candidate_id or candidate.id
            sources.append(
                CaptureSource(
                    candidate_id=candidate.id,
                    device_id=candidate.device_id,
                    transaction_group_id=str(group),
                    storage_key=binary.storage_key,
                    content_type=binary.content_type,
                    content_hash=binary.content_hash,
                    resolution=candidate.resolution_result,
                )
            )
        return sources

    async def add_example(self, example: TrainingExample) -> None:
        self.session.add(
            TrainingExampleRow(
                id=example.id,
                candidate_id=example.candidate_id,
                device_id=example.device_id,
                transaction_group_id=example.transaction_group_id,
                split=example.split,
                image_key=example.image_key,
                image_sha256=example.image_sha256,
                content_type=example.content_type,
                labels=example.labels,
                label_version=example.label_version,
                created_at=example.created_at,
            )
        )
        await self.session.flush()

    async def examples(self) -> list[TrainingExample]:
        rows = await self.session.scalars(
            select(TrainingExampleRow).order_by(
                TrainingExampleRow.created_at, TrainingExampleRow.id
            )
        )
        return [to_example(row) for row in rows]

    async def summary(self) -> dict[str, int]:
        counts = dict(
            (
                await self.session.execute(
                    select(TrainingExampleRow.split, func.count()).group_by(
                        TrainingExampleRow.split
                    )
                )
            ).tuples()
        )
        groups = await self.session.scalar(
            select(func.count(distinct(TrainingExampleRow.transaction_group_id)))
        )
        devices = await self.session.scalar(
            select(func.count())
            .select_from(TrainingConsentRow)
            .where(TrainingConsentRow.enabled.is_(True))
        )
        return {
            "train_examples": int(counts.get("TRAIN", 0)),
            "holdout_examples": int(counts.get("HOLDOUT", 0)),
            "transaction_groups": int(groups or 0),
            "consenting_devices": int(devices or 0),
        }

    async def delete_device(self, device_id: UUID) -> tuple[int, list[str]]:
        keys = set(
            await self.session.scalars(
                select(TrainingExampleRow.image_key).where(
                    TrainingExampleRow.device_id == device_id
                )
            )
        )
        result = await self.session.execute(
            delete(TrainingExampleRow).where(TrainingExampleRow.device_id == device_id)
        )
        still_used = set(
            await self.session.scalars(
                select(TrainingExampleRow.image_key).where(
                    TrainingExampleRow.image_key.in_(keys)
                )
            )
        ) if keys else set()
        return int(getattr(result, "rowcount", 0) or 0), sorted(keys - still_used)
