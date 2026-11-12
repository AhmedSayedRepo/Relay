"""Bank SMS tab, dashboard time range, and the daily report email."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from relay.infrastructure.daily_report import render, send_daily_report
from tests.test_events import event, headers, provision

CAIRO = ZoneInfo("Africa/Cairo")


async def _send_sms(client, bundle, body, occurred):
    item = event(bundle["installation_id"], body=body)
    item["occurred_at"] = occurred.astimezone(UTC).isoformat().replace("+00:00", "Z")
    response = await client.post(
        "/api/v1/device/events/batch", headers=headers(bundle), json={"events": [item]}
    )
    assert response.json()["results"][0]["status"] == "accepted", response.text


async def test_bank_sms_lists_unused_and_amountless_messages(client, auth_headers):
    _, bundle = await provision(client, auth_headers, "bank-sms-list-phone")
    now = datetime.now(UTC) - timedelta(minutes=5)
    await _send_sms(
        client, bundle, "notice: You have received EGP 640.00 via InstaPay", now
    )
    await _send_sms(client, bundle, "notice: your verification code is 482913", now)
    today = datetime.now(CAIRO).date()
    listed = await client.get(
        "/api/v1/bank-sms",
        headers=auth_headers,
        params={
            "date_from": (today - timedelta(days=1)).isoformat(),
            "date_to": today.isoformat(),
        },
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["counts"] == {"MATCHED": 0, "UNUSED": 1, "NO_AMOUNT": 1}
    assert body["unused_totals"] == [{"currency": "EGP", "amount": "640.00"}]
    assert all("body" not in row for row in body["items"])
    only_unused = await client.get(
        "/api/v1/bank-sms",
        headers=auth_headers,
        params={
            "date_from": (today - timedelta(days=1)).isoformat(),
            "date_to": today.isoformat(),
            "status": "UNUSED",
        },
    )
    rows = only_unused.json()["items"]
    assert [row["amount"] for row in rows] == ["640.00"]


async def test_dashboard_accepts_a_time_range(client, auth_headers):
    today = datetime.now(CAIRO).date().isoformat()
    response = await client.get(
        "/api/v1/dashboard",
        headers=auth_headers,
        params={
            "date_from": today,
            "date_to": today,
            "time_from": "00:00",
            "time_to": "23:59",
        },
    )
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["review_totals_by_currency"], list)


def test_daily_report_is_readable():
    summary = {
        "totals_by_currency": [
            {"currency": "EGP", "amount": "1500.00", "transaction_count": 3}
        ],
        "final_transaction_count": 3,
        "needs_review_count": 2,
        "sms_by_status": {"VERIFIED": 2},
        "transactions_by_device": [
            {
                "device_name": "Counter <1>",
                "count": 4,
                "confirmed_count": 3,
                "sms_verified_count": 2,
                "confirmed_amount": "1500.00",
            }
        ],
    }
    sms = {"counts": {"MATCHED": 2, "UNUSED": 1, "NO_AMOUNT": 0}, "unused_totals": []}
    text, html = render(date(2026, 11, 12), summary, sms)
    assert "Relay daily report: 2026-11-12" in text
    assert "1,500.00 EGP" in text and "Counter <1>: 4 / 3 / 2" in text
    assert "Counter &lt;1&gt;" in html and "<table" in html


async def test_daily_report_waits_for_the_last_hour(app):
    early = datetime(2026, 11, 12, 10, 0, tzinfo=CAIRO)
    assert await send_daily_report(app.state.settings, now=early) is False
