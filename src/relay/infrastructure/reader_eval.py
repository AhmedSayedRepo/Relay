"""Score the text reader on receipts whose answers are already known.

Every receipt confirmed by a bank SMS or by an operator is a test case: its
confirmed amount (and reference, when verified) is the expected answer, and
it was paid to the owner, so the recipient check must say "match". The
reader is re-run on the text stored for each receipt, so a full check takes
seconds and no image leaves the server.

The result is compared receipt by receipt with the last accepted score. A
change that breaks more receipts than it fixes, or reads any amount wrong,
fails; deploy/update.sh then keeps the running version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from relay.domain.processing import ReviewStatus
from relay.infrastructure.config import Settings
from relay.infrastructure.database import Database
from relay.infrastructure.processing_models import ReceiptCandidateRow

FIELDS = ("amount", "reference", "recipient", "kept")
OPERATOR = {"OPERATOR_CONFIRMED", "OPERATOR_CORRECTED"}


def split_passes(text: str) -> tuple[str, str | None]:
    """Stored text is block + sparse + sparse (see text_recheck.signals);
    give back (block + sparse, sparse) to re-run the reader as it ran."""
    for size in range(len(text) // 2, 0, -1):
        tail = text[-size:]
        ending = "\n" + tail + "\n" + tail
        if text.endswith(ending):
            block = text[: -len(ending)]
            return block + "\n" + tail, tail
    return text, None


def expected(resolution: Any) -> dict[str, str] | None:
    """The known answers for a confirmed receipt, or None if not confirmed."""
    if not isinstance(resolution, dict):
        return None
    fields = resolution.get("fields")
    fields = fields if isinstance(fields, dict) else {}

    def value(name: str) -> str | None:
        field = fields.get(name)
        found = field.get("value") if isinstance(field, dict) else None
        return str(found) if found not in (None, "") else None

    def trusted(name: str) -> bool:
        field = fields.get(name)
        return isinstance(field, dict) and field.get("trust") in OPERATOR

    auto = resolution.get("auto_confirmed")
    answers: dict[str, str] = {}
    if isinstance(auto, dict) and auto.get("rule") == "sms_day":
        verified = auto.get("trained_fields")
        verified = verified if isinstance(verified, list) else []
        if value("amount"):
            answers["amount"] = str(value("amount"))
        if "reference_number" in verified and value("reference_number"):
            answers["reference"] = str(value("reference_number"))
    elif resolution.get("state") == "REVIEW_FINALIZED":
        if trusted("amount") and value("amount"):
            answers["amount"] = str(value("amount"))
        if trusted("reference_number") and value("reference_number"):
            answers["reference"] = str(value("reference_number"))
    else:
        return None
    return answers or None


def outcome(want: str | None, got: str | None) -> str:
    if want is None:
        return "skip"
    if got is None:
        return "miss"
    return "ok" if _same(want, got) else "wrong"


def _same(want: str, got: str) -> bool:
    try:
        return float(want) == float(got)
    except ValueError:
        return want.strip().upper() == got.strip().upper()


def grade(
    text: str,
    answers: dict[str, str],
    owner: tuple[str, str | None] | None,
    single: bool = False,
) -> dict[str, str]:
    """Re-read one receipt's stored text and grade each field. ``single``:
    the text is one reading (the model reader), not the two Tesseract passes."""
    from relay.infrastructure.cli import _receipt_like, _text_amount
    from relay.infrastructure.text_recheck import VERSION, recipient_check, signals

    block, sparse = (text, None) if single else split_passes(text)
    read = signals(block, sparse)
    values = read.get("values") or {}
    found = read.get("found") or {}
    result = {
        "amount": outcome(answers.get("amount"), _text_amount({VERSION: read})),
        "reference": outcome(answers.get("reference"), values.get("reference_number")),
        "kept": "ok" if _receipt_like(read) else "wrong",
        "recipient": "skip",
    }
    if owner is not None:
        name, phone = owner
        verdict = recipient_check(
            str(read.get("text") or ""), name, phone, bool(found.get("brand"))
        )
        result["recipient"] = (
            "ok" if verdict == "match" else "miss" if verdict == "unclear" else "wrong"
        )
    return result


def compare(
    before: dict[str, dict[str, str]], now: dict[str, dict[str, str]]
) -> tuple[bool, list[str]]:
    """Receipt-by-receipt changes against the accepted score.

    Fails when a field breaks more receipts than it fixes, or when any
    receipt newly reads a wrong amount or would now be hidden."""
    lines: list[str] = []
    passed = True
    for field in FIELDS:
        broke = [
            key
            for key, grades in now.items()
            if before.get(key, {}).get(field) == "ok" and grades[field] != "ok"
        ]
        fixed = [
            key
            for key, grades in now.items()
            if key in before
            and before[key].get(field) not in ("ok", "skip", None)
            and grades[field] == "ok"
        ]
        newly_wrong = [
            key
            for key, grades in now.items()
            if grades[field] == "wrong" and before.get(key, {}).get(field) != "wrong"
        ]
        if broke or fixed:
            lines.append(f"{field}: fixed {len(fixed)}, broke {len(broke)}")
        for key in broke:
            lines.append(f"  broke {field}: {key}")
        if len(broke) > len(fixed):
            passed = False
        if field in ("amount", "kept") and newly_wrong:
            passed = False
            lines.extend(f"  now wrong {field}: {key}" for key in newly_wrong)
    return passed, lines


def summary(grades: dict[str, dict[str, str]]) -> list[str]:
    lines = [f"Confirmed receipts checked: {len(grades)}"]
    for field in FIELDS:
        counts = {"ok": 0, "miss": 0, "wrong": 0}
        for grade_ in grades.values():
            if grade_[field] in counts:
                counts[grade_[field]] += 1
        total = sum(counts.values())
        share = f"{100 * counts['ok'] / total:.0f}%" if total else "-"
        lines.append(
            f"  {field:<9} {share:>4} right · {counts['miss']} missed · "
            f"{counts['wrong']} wrong (of {total})"
        )
    return lines


Grades = dict[str, dict[str, str]]
MODEL_PREFIXES = ("vl_", "ppocr_")
MODEL_NAMES = {
    "vl_ocr_v1": "PaddleOCR-VL",
    "vl_glm_v1": "GLM-OCR",
    "ppocr_v4": "PaddleOCR (classic)",
}


async def confirmed_grades(settings: Settings) -> tuple[Grades, dict[str, Grades]]:
    """Grade the text reader, and the model reader where it has read, on
    every confirmed receipt, keyed by receipt id."""
    from relay.infrastructure.email_settings import recipient
    from relay.infrastructure.text_recheck import VERSION

    database = Database(settings)
    try:
        async with database.session() as session:
            owner = await recipient(session)
            rows = (
                await session.execute(
                    select(
                        ReceiptCandidateRow.id,
                        ReceiptCandidateRow.resolution_result,
                        ReceiptCandidateRow.machine_result,
                    ).where(
                        ReceiptCandidateRow.review_status != ReviewStatus.REJECTED.value
                    )
                )
            ).all()
    finally:
        await database.close()
    grades: Grades = {}
    models: dict[str, Grades] = {}
    for candidate_id, resolution, machine in rows:
        answers = expected(resolution)
        if answers is None or not isinstance(machine, dict):
            continue
        read = machine.get(VERSION)
        if isinstance(read, dict) and read.get("text"):
            grades[str(candidate_id)] = grade(str(read["text"]), answers, owner)
        # Every model reader that has read it (vl_ocr_v1, vl_glm_v1, ...).
        for key, seen in machine.items():
            if (
                key.startswith(MODEL_PREFIXES)
                and isinstance(seen, dict)
                and seen.get("text")
            ):
                models.setdefault(key, {})[str(candidate_id)] = grade(
                    str(seen["text"]), answers, owner, single=True
                )
    return grades, models


def evaluate_reader(settings: Settings, score: Path | None, accept: bool) -> int:
    """Print the reader's score; with a score file, gate on it.

    Returns 0 to go ahead, 1 when the reader got worse (and ``accept`` is
    not set). An improved or equal score replaces the saved one."""
    import asyncio

    grades, models = asyncio.run(confirmed_grades(settings))
    for line in summary(grades):
        print(line)
    for version, model in sorted(models.items()):
        both = {key: grades[key] for key in model if key in grades}
        name = MODEL_NAMES.get(version, version)
        print(f"\nSame {len(both)} receipts, current reader vs {name}:")
        for line in summary(both)[1:]:
            print("  current" + line)
        for line in summary({key: model[key] for key in both})[1:]:
            print("  model  " + line)
    if score is None:
        return 0
    before: dict[str, dict[str, str]] = {}
    if score.exists():
        saved = json.loads(score.read_text(encoding="utf-8"))
        before = saved if isinstance(saved, dict) else {}
    passed, lines = compare(before, grades)
    for line in lines:
        print(line)
    if not passed and not accept:
        print("Reader got worse on confirmed receipts. Not saving this score.")
        return 1
    score.write_text(json.dumps(grades, sort_keys=True), encoding="utf-8")
    print("Reader score saved." if passed else "Worse score accepted and saved.")
    return 0
