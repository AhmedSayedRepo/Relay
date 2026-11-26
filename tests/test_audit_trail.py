from io import BytesIO

from openpyxl import load_workbook

from tests.test_auto_confirm import _link_sms, _now, _read_and_confirm
from tests.test_evidence_backend import run_receipt
from tests.test_sms_verification import _send_sms


async def test_the_trail_shows_who_confirmed_and_sent_back(client, auth_headers, app):
    first, _ = await run_receipt(client, auth_headers, app, "trail-a")
    second, _ = await run_receipt(client, auth_headers, app, "trail-b")
    bundle = await _link_sms(client, auth_headers, [first, second], "trail-phone")
    await _send_sms(
        client, bundle, "notice: You have received EGP 641.00 via InstaPay", _now()
    )
    same = {"amount": "641.00", "currency": "EGP", "reference_number": "730492013227"}
    await _read_and_confirm(app, same)
    # Relay confirmed the first and rejected the second as its copy; a person
    # then sends the first back to Review.
    reopened = await client.post(
        f"/api/v1/receipts/{first['id']}/reopen", headers=auth_headers
    )
    assert reopened.status_code == 204

    listed = await client.get("/api/v1/audit-trail", headers=auth_headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    mine = {(i["candidate_id"], i["action"]): i for i in items}
    confirmed = mine[(first["id"], "confirmed")]
    assert confirmed["automatic"] is True and confirmed["actor"] is None
    assert confirmed["rule"] == "sms_same_amount"
    assert confirmed["amount"] == "641.00"
    copy = mine[(second["id"], "rejected_copy")]
    assert copy["automatic"] is True and copy["rule"] == "copy_of_confirmed"
    back = mine[(first["id"], "sent_back")]
    assert back["automatic"] is False and back["actor"]
    assert items == sorted(items, key=lambda i: i["at"], reverse=True)

    people = await client.get(
        "/api/v1/audit-trail", params={"actor": "automatic"}, headers=auth_headers
    )
    assert all(i["automatic"] for i in people.json()["items"])
    only = await client.get(
        "/api/v1/audit-trail", params={"action": "sent_back"}, headers=auth_headers
    )
    assert [i["candidate_id"] for i in only.json()["items"]] == [first["id"]]

    exported = await client.get(
        "/api/v1/audit-trail/export.xlsx",
        params={"language": "ar"},
        headers=auth_headers,
    )
    assert exported.status_code == 200
    sheet = load_workbook(BytesIO(exported.content)).active
    assert sheet.sheet_view.rightToLeft is True
    assert sheet["A1"].value == "الوقت"
    assert "تلقائي" in [cell.value for cell in sheet["C"]]
