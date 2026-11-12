"""Admin-only email settings: sender name, Gmail address and app password."""

import smtplib
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, SecretStr

from relay.application.account_recovery_ports import OutgoingEmail
from relay.application.errors import Conflict
from relay.infrastructure.config import Settings
from relay.infrastructure.email_settings import load_row, mail_config, save
from relay.infrastructure.mailer import SmtpMailer
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import (
    AuthenticatedUser,
    current_user,
    unit_of_work,
)
from relay.presentation.schemas import InputModel

router = APIRouter(
    prefix="/settings", tags=["Settings"], dependencies=[Depends(current_user)]
)
Uow = Annotated[SqlUnitOfWork, Depends(unit_of_work)]


class EmailSettingsResponse(BaseModel):
    sender_name: str
    sender_email: str
    report_email: str | None
    password_set: bool
    # portal: these settings; server: SMTP from the server's .env; none: off.
    source: str


class EmailSettingsRequest(InputModel):
    sender_name: str = Field(min_length=1, max_length=120)
    sender_email: EmailStr = Field(max_length=254)
    # Empty keeps the saved app password.
    app_password: SecretStr | None = Field(default=None, max_length=200)
    report_email: EmailStr | None = Field(default=None, max_length=254)


class TestEmailResponse(BaseModel):
    sent_to: str


async def _view(request: Request, uow: SqlUnitOfWork) -> EmailSettingsResponse:
    settings = cast(Settings, request.app.state.settings)
    row = await load_row(uow.session)
    config = await mail_config(uow.session, settings)
    source = "none"
    if config is not None:
        source = "server"
        if row is not None and config.sender_email == row.sender_email:
            source = "portal"
    return EmailSettingsResponse(
        sender_name=row.sender_name if row else "Relay",
        sender_email=row.sender_email if row else (settings.smtp_from or ""),
        report_email=(row.report_email if row else None)
        or settings.daily_report_email,
        password_set=bool(row and row.password_encrypted),
        source=source,
    )


@router.get("/email", response_model=EmailSettingsResponse)
async def get_email_settings(request: Request, uow: Uow) -> EmailSettingsResponse:
    return await _view(request, uow)


@router.put("/email", response_model=EmailSettingsResponse)
async def put_email_settings(
    body: EmailSettingsRequest, request: Request, uow: Uow, user: AuthenticatedUser
) -> EmailSettingsResponse:
    settings = cast(Settings, request.app.state.settings)
    row = await load_row(uow.session)
    # Gmail shows app passwords in groups of four; spaces are not part of it.
    password = (
        "".join(body.app_password.get_secret_value().split())
        if body.app_password
        else ""
    )
    if not password and (row is None or not row.password_encrypted):
        raise Conflict("Enter the Gmail app password.")
    await save(
        uow.session,
        settings,
        sender_name=body.sender_name.strip(),
        sender_email=str(body.sender_email).strip().lower(),
        password=password or None,
        report_email=str(body.report_email).strip().lower()
        if body.report_email
        else None,
        actor_id=user.id,
    )
    return await _view(request, uow)


@router.post("/email/test", response_model=TestEmailResponse)
async def send_test_email(
    request: Request, uow: Uow, user: AuthenticatedUser
) -> TestEmailResponse:
    settings = cast(Settings, request.app.state.settings)
    config = await mail_config(uow.session, settings)
    if config is None:
        raise Conflict("Save the email settings first.")
    to = config.report_email or user.email
    body = (
        "This is a test email from Relay. Your email settings work.\n\n"
        "هذه رسالة تجريبية من Relay. إعدادات البريد تعمل."
    )
    try:
        await SmtpMailer(config).send(OutgoingEmail(to, "Relay test email", body))
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gmail rejected the address or app password.",
        ) from None
    except (OSError, smtplib.SMTPException):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Could not reach the email server. Try again.",
        ) from None
    return TestEmailResponse(sent_to=to)
