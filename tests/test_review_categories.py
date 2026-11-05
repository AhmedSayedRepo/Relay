from relay.domain.review_categories import categorize


def field(value=None, trust="MACHINE_ONLY"):
    candidates = [{"value": value, "normalized_value": value}] if value else []
    return {"value": value, "trust": trust, "candidates": candidates}


def row(identifier, reference=None, date=None, trust="MACHINE_ONLY"):
    fields = {
        "reference_number": field(reference),
        "amount": field("100.00", trust),
        "currency": field("EGP"),
        "date": field(date),
        "time": field("12:00:00" if date else None),
    }
    blocking = [
        name for name, item in fields.items() if item["trust"] == "MACHINE_ONLY"
    ]
    return {
        "id": identifier,
        "resolution_result": {"fields": fields, "blocking_fields": blocking},
    }


def test_categories_follow_the_work_needed() -> None:
    done = row("done", None, None)
    for name in done["resolution_result"]["blocking_fields"]:
        if name in ("reference_number", "date", "time"):
            done["resolution_result"]["fields"][name]["reason"] = "operator_unavailable"
        else:
            done["resolution_result"]["fields"][name]["trust"] = "OPERATOR_CONFIRMED"
    done["resolution_result"]["blocking_fields"] = ["reference_number", "date", "time"]
    rows = [
        row("ready", "111111111111", "2026-10-27"),
        row("missing", None, None),
        row("dup-a", "222222222222", "2026-10-27"),
        row("dup-b", "222222222222", "2026-10-27"),
        row("started", None, None, trust="OPERATOR_CONFIRMED"),
        done,
    ]
    assert categorize(rows) == {
        "ready": "ready",
        "missing": "missing",
        "dup-a": "duplicate",
        "dup-b": "duplicate",
        "started": "in_progress",
        "done": "done",
    }
