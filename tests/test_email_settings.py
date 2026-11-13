"""Portal email settings: sender name, Gmail address and app password."""

from email.message import EmailMessage
from typing import Any

from relay.infrastructure.email_settings import decrypt, load_row
from relay.infrastructure.mailer import SmtpMailer


async def test_admin_saves_gmail_sender_and_sends_a_test(
    app: Any, client: Any, auth_headers: dict[str, str], monkeypatch: Any
) -> None:
    url = "/api/v1/settings/email"
    body = {"sender_name": "Relay - Ahmed", "sender_email": "Relay.Sender@gmail.com"}
    missing = await client.put(url, headers=auth_headers, json=body)
    assert missing.status_code == 409
    full = body | {"app_password": "abcd efgh ijkl mnop"}
    full["report_email"] = "boss@example.com"
    saved = await client.put(url, headers=auth_headers, json=full)
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert view == {
        "sender_name": "Relay - Ahmed",
        "sender_email": "relay.sender@gmail.com",
        "report_email": "boss@example.com",
        "password_set": True,
        "source": "portal",
    }
    async with app.state.database.session() as session:
        row = await load_row(session)
        assert row is not None and "abcd" not in (row.password_encrypted or "")
        assert decrypt(app.state.settings, row.password_encrypted) == (
            "abcdefghijklmnop"
        )
    # Saving again without a password keeps the stored one.
    keep = body | {"report_email": "boss@example.com"}
    again = await client.put(url, headers=auth_headers, json=keep)
    assert again.status_code == 200 and again.json()["password_set"] is True

    sent: list[EmailMessage] = []

    def deliver(self: SmtpMailer, message: EmailMessage) -> None:
        sent.append(message)

    monkeypatch.setattr(SmtpMailer, "_deliver", deliver)
    test = await client.post(url + "/test", headers=auth_headers)
    assert test.status_code == 200, test.text
    assert test.json() == {"sent_to": "boss@example.com"}
    assert sent[0]["From"] == "Relay - Ahmed <relay.sender@gmail.com>"


async def test_email_settings_need_a_signed_in_admin(client: Any) -> None:
    assert (await client.get("/api/v1/settings/email")).status_code == 401
