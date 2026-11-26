"""Copies of a receipt (the same reference sent again, usually forwarded in
the WhatsApp group) wait in Review beside the receipt they copy. A reviewer
rejects them as copies, one by one or several at once, or marks one as a
different payment so it is checked like any other receipt."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.errors import Conflict, NotFound
from relay.domain.processing import ReviewStatus
from relay.infrastructure.database import Database
from relay.infrastructure.processing_models import (
    ReceiptCandidateRow,
    ReceiptReviewAuditRow,
)


async def unhide_copies(database: Database) -> int:
    """Copies used to be hidden; they now wait in Review beside the first,
    flagged as a repeat of it so they show under "possible duplicate"."""
    done = 0
    async with database.session() as session:
        rows = await session.scalars(
            select(ReceiptCandidateRow)
            .where(
                ReceiptCandidateRow.review_status == ReviewStatus.NEEDS_REVIEW.value,
                ReceiptCandidateRow.hidden_reason == "same_receipt",
            )
            .with_for_update()
        )
        for row in rows:
            resolution = copy.deepcopy(row.resolution_result or {})
            other = resolution.get("copy_of")
            if other and not isinstance(resolution.get("possible_repeat"), dict):
                resolution["possible_repeat"] = {"kind": "reference", "with": other}
                row.resolution_result = resolution
            row.hidden_reason = None
            row.updated_at = datetime.now(UTC)
            done += 1
        await session.commit()
    return done


async def reject_copies(
    session: AsyncSession, ids: list[UUID], actor_id: UUID, automatic: bool = False
) -> int:
    """Reject receipts waiting in Review as copies of another receipt;
    ``automatic`` when Relay does it (shown so in the audit trail)."""
    now = datetime.now(UTC)
    done = 0
    for candidate_id in dict.fromkeys(ids):
        row = await session.scalar(
            select(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.id == candidate_id)
            .with_for_update()
        )
        if row is None or row.review_status != ReviewStatus.NEEDS_REVIEW.value:
            continue
        before = {
            "review_status": row.review_status,
            "resolution_result": copy.deepcopy(row.resolution_result),
        }
        resolution = copy.deepcopy(row.resolution_result or {})
        repeat = resolution.get("possible_repeat")
        other = repeat.get("with") if isinstance(repeat, dict) else None
        resolution["state"] = "REJECTED_OR_UNKNOWN_RECEIPT"
        resolution["rejected_reason"] = "copy"
        if other:
            resolution["copy_of"] = other
        row.resolution_result = resolution
        row.review_status = ReviewStatus.REJECTED.value
        row.reviewed_result = {"review_status": "REJECTED", "reason": "copy"}
        row.updated_at = now
        session.add(
            ReceiptReviewAuditRow(
                id=uuid4(),
                candidate_id=row.id,
                reviewer_id=actor_id,
                action="REJECT",
                before_result=before,
                after_result={"review_status": "REJECTED", "reason": "copy"}
                | ({"automatic": True} if automatic else {}),
                created_at=now,
                expires_at=row.expires_at,
            )
        )
        done += 1
    await session.commit()
    return done


async def not_a_copy(session: AsyncSession, candidate_id: UUID, actor_id: UUID) -> None:
    """A different payment after all: checked like any other receipt."""
    row = await session.scalar(
        select(ReceiptCandidateRow)
        .where(ReceiptCandidateRow.id == candidate_id)
        .with_for_update()
    )
    if row is None:
        raise NotFound("Receipt candidate not found.")
    if row.review_status != ReviewStatus.NEEDS_REVIEW.value:
        raise Conflict("Only a receipt waiting in Review can be marked.")
    resolution = copy.deepcopy(row.resolution_result or {})
    resolution.pop("possible_repeat", None)
    resolution.pop("copy_of", None)
    resolution["repeat_cleared"] = {
        "user": str(actor_id),
        "at": datetime.now(UTC).isoformat(),
    }
    row.resolution_result = resolution
    row.updated_at = datetime.now(UTC)
    await session.commit()
