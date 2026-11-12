import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from relay.application.account_recovery_ports import OutgoingEmail
from relay.infrastructure.config import Settings

logger = logging.getLogger(__name__)


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host or not settings.smtp_from:
            raise ValueError("SMTP is not configured.")
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.security = settings.smtp_security
        self.username = settings.smtp_username
        self.password = settings.smtp_password
        self.sender = settings.smtp_from

    async def send(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
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
        context = ssl.create_default_context()
        client: smtplib.SMTP
        if self.security == "ssl":
            client = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=context)
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=20)
        with client:
            if self.security == "starttls":
                client.starttls(context=context)
            if self.username and self.password:
                client.login(self.username, self.password.get_secret_value())
            client.send_message(message)


async def send_quietly(mailer: SmtpMailer, email: OutgoingEmail) -> None:
    # Runs after the response; failures are logged without the address or link.
    try:
        await mailer.send(email)
    except (OSError, smtplib.SMTPException):
        logger.warning("password_reset.email_failed")
