import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Any, Literal

from relay.application.account_recovery_ports import OutgoingEmail

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MailConfig:
    host: str
    port: int
    security: Literal["starttls", "ssl"]
    username: str | None
    password: str | None = field(repr=False)
    sender_email: str
    sender_name: str
    report_email: str | None = None


def html_page(title: str, body: str) -> str:
    """Wrap simple paragraphs in a clean, readable email layout."""
    paragraphs = "".join(
        f'<p style="margin:0 0 14px;line-height:1.6">{escape(part)}</p>'
        for part in body.split("\n\n")
        if part.strip()
    )
    return (
        '<!doctype html><html><body style="margin:0;padding:24px;'
        "background:#f4f6f3;font-family:Segoe UI,Tahoma,Arial,sans-serif;"
        'color:#103e46"><div style="max-width:560px;margin:auto;background:#fff;'
        'border:1px solid #dce7e2;border-radius:12px;padding:24px">'
        f'<h1 style="font-size:20px;margin:0 0 16px">{escape(title)}</h1>'
        f"{paragraphs}</div></body></html>"
    )


class SmtpMailer:
    def __init__(self, config: MailConfig) -> None:
        self.config = config
        self.sender = formataddr((config.sender_name.strip(), config.sender_email))

    async def send(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        message.add_alternative(html_page(email.subject, email.body), subtype="html")
        await asyncio.to_thread(self._deliver, message)

    async def send_report(
        self,
        to: str,
        subject: str,
        text: str,
        html: str,
        attachment: tuple[str, bytes] | None = None,
    ) -> None:
        """A readable HTML email (with a plain-text fallback) and an optional
        Excel attachment."""
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        if attachment is not None:
            name, data = attachment
            message.add_attachment(
                data,
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=name,
            )
        await asyncio.to_thread(self._deliver, message)

    def _deliver(self, message: EmailMessage) -> None:
        config = self.config
        context = ssl.create_default_context()
        client: smtplib.SMTP
        if config.security == "ssl":
            client = smtplib.SMTP_SSL(
                config.host, config.port, timeout=20, context=context
            )
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=20)
        with client:
            if config.security == "starttls":
                client.starttls(context=context)
            if config.username and config.password:
                client.login(config.username, config.password)
            client.send_message(message)


async def send_quietly(mailer: Any, email: OutgoingEmail) -> None:
    # Runs after the response; failures are logged without the address or link.
    try:
        await mailer.send(email)
    except (OSError, smtplib.SMTPException):
        logger.warning("password_reset.email_failed")
