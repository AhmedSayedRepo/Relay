"""Operator-review requests shared by legacy regression tests."""

from uuid import uuid4


async def review_field(client, headers, candidate, field, action, value=None):
    response = await client.post(
        f"/api/v1/receipts/{candidate['id']}/exceptions",
        headers=headers,
        json={
            "command_id": str(uuid4()),
            "expected_updated_at": candidate["updated_at"],
            "field": field,
            "action": action,
            "value": value,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def review_critical_fields(client, headers, candidate, overrides=None):
    """Exercise explicit decisions; never promote machine suggestions directly."""
    overrides = overrides or {}
    for field, default in (
        ("reference_number", "ABC123456"),
        ("amount", "1250.50"),
        ("currency", "EGP"),
        ("date", "2026-09-05"),
        ("time", "14:35"),
    ):
        if field not in candidate["resolution_result"]["blocking_fields"]:
            continue
        value = overrides.get(field, default)
        machine = candidate["resolution_result"]["fields"].get(field, {})
        suggestions = {machine.get("value")}
        suggestions.update(
            item.get("normalized_value")
            for item in machine.get("candidates", [])
            if isinstance(item, dict)
        )
        action = "CONFIRM" if value in suggestions else "CORRECT"
        candidate = await review_field(client, headers, candidate, field, action, value)
    return candidate
