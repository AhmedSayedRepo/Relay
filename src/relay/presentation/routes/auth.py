import hashlib
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, SecretStr

from relay.application.account_recovery import AccountRecoveryService
from relay.application.auth import AuthService
from relay.application.errors import AuthenticationFailed
from relay.application.ports import AccessToken
from relay.domain.users import User
from relay.infrastructure.config import Settings
from relay.infrastructure.email_settings import mail_config
from relay.infrastructure.mailer import SmtpMailer, send_quietly
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.security import Argon2Passwords, JwtTokens
from relay.presentation.dependencies import (
    AuthenticatedUser,
    auth_service,
    unit_of_work,
)
from relay.presentation.schemas import (
    InputModel,
    LoginRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

NewPassword = Annotated[SecretStr, Field(min_length=12, max_length=1024)]


async def outgoing_mailer(request: Request, uow: SqlUnitOfWork) -> Any:
    """The mailer for reset links: a test fake, else the portal's email
    settings, else the server's SMTP settings. None when email is off."""
    if request.app.state.mailer is not None:
        return request.app.state.mailer
    settings = cast(Settings, request.app.state.settings)
    config = await mail_config(uow.session, settings)
    return SmtpMailer(config) if config is not None else None


async def recovery_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> AccountRecoveryService:
    settings = cast(Settings, request.app.state.settings)
    ready = bool(settings.operator_public_url) and (
        await outgoing_mailer(request, uow) is not None
    )
    return AccountRecoveryService(
        uow,
        cast(Argon2Passwords, request.app.state.passwords),
        public_url=settings.operator_public_url if ready else None,
        reset_minutes=settings.password_reset_minutes,
    )


Recovery = Annotated[AccountRecoveryService, Depends(recovery_service)]


class RecoveryOptionsResponse(BaseModel):
    email_reset: bool


class ResetRequest(InputModel):
    email: EmailStr = Field(max_length=254)


class ResetConfirmRequest(InputModel):
    token: str = Field(min_length=20, max_length=128)
    new_password: NewPassword


class PasswordChangeRequest(InputModel):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: NewPassword


SESSION_COOKIE = "relay_session"


def _fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def _set_session(
    request: Request, response: Response, user_id: UUID, password_hash: str
) -> None:
    settings = cast(Settings, request.app.state.settings)
    tokens = cast(JwtTokens, request.app.state.tokens)
    response.set_cookie(
        SESSION_COOKIE,
        tokens.issue_session(user_id, _fingerprint(password_hash)),
        max_age=settings.session_idle_hours * 3600,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/api/v1/auth",
    )


def _token_response(token: AccessToken) -> TokenResponse:
    return TokenResponse(
        access_token=token.access_token,
        token_type=token.token_type,
        expires_in=token.expires_in,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(auth_service)],
) -> TokenResponse:
    token = await service.login(str(body.email), body.password.get_secret_value())
    user = await service.uow.users.get_by_email(str(body.email).strip().lower())
    if user is not None:
        _set_session(request, response, user.id, user.password_hash)
    return _token_response(token)


@router.post("/session", response_model=TokenResponse)
async def resume_session(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(auth_service)],
) -> TokenResponse:
    """Renew the signed-in session from its httpOnly cookie: survives page
    reloads and a phone putting the browser to sleep, until it sits idle."""
    tokens = cast(JwtTokens, request.app.state.tokens)
    value = request.cookies.get(SESSION_COOKIE, "")
    user: User | None = None
    user_id: UUID | None = None
    fingerprint = ""
    if value and len(value) <= 4096:
        try:
            user_id, fingerprint = tokens.session_subject(value)
        except AuthenticationFailed:
            user_id = None
        if user_id is not None:
            user = await service.uow.users.get(user_id)
            if user is not None and (
                not user.is_active or fingerprint != _fingerprint(user.password_hash)
            ):
                user = None
    if user is None:
        raise AuthenticationFailed("Session expired.")
    _set_session(request, response, user.id, user.password_hash)
    return _token_response(await service.refresh(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/api/v1/auth")
    return response


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    user: AuthenticatedUser, service: Annotated[AuthService, Depends(auth_service)]
) -> TokenResponse:
    """Sliding session: a valid token can be exchanged for a new one."""
    token = await service.refresh(user)
    return TokenResponse(
        access_token=token.access_token,
        token_type=token.token_type,
        expires_in=token.expires_in,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: AuthenticatedUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/recovery-options", response_model=RecoveryOptionsResponse)
async def recovery_options(service: Recovery) -> RecoveryOptionsResponse:
    return RecoveryOptionsResponse(email_reset=service.public_url is not None)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_reset(
    body: ResetRequest,
    request: Request,
    service: Recovery,
    background: BackgroundTasks,
) -> dict[str, str]:
    email = await service.request_reset(str(body.email))
    mailer = await outgoing_mailer(request, cast(SqlUnitOfWork, service.uow))
    if email is not None and mailer is not None:
        background.add_task(send_quietly, mailer, email)
    # Same answer whether or not the account exists.
    return {"status": "accepted"}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_reset(body: ResetConfirmRequest, service: Recovery) -> Response:
    await service.confirm_reset(body.token, body.new_password.get_secret_value())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    user: AuthenticatedUser,
    service: Recovery,
    request: Request,
    auth: Annotated[AuthService, Depends(auth_service)],
) -> Response:
    await service.change_password(
        user,
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    # Other sessions end with the old password; this one carries on.
    fresh = await auth.uow.users.get(user.id)
    if fresh is not None:
        _set_session(request, response, fresh.id, fresh.password_hash)
    return response
