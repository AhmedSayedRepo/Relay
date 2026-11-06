from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, SecretStr

from relay.application.account_recovery import AccountRecoveryService
from relay.application.auth import AuthService
from relay.infrastructure.config import Settings
from relay.infrastructure.mailer import SmtpMailer, send_quietly
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.security import Argon2Passwords
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


def recovery_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> AccountRecoveryService:
    settings = cast(Settings, request.app.state.settings)
    return AccountRecoveryService(
        uow,
        cast(Argon2Passwords, request.app.state.passwords),
        public_url=(
            settings.operator_public_url if settings.email_reset_enabled else None
        ),
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


class RecoveryCodeRequest(InputModel):
    code: str = Field(min_length=12, max_length=32)
    new_password: NewPassword | None = None


class RecoveryCodeResponse(BaseModel):
    email: str


class PasswordConfirmation(InputModel):
    password: SecretStr = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(InputModel):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: NewPassword


class RecoveryCodesResponse(BaseModel):
    codes: list[str]


class RecoveryStatusResponse(BaseModel):
    remaining: int


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, service: Annotated[AuthService, Depends(auth_service)]
) -> TokenResponse:
    token = await service.login(str(body.email), body.password.get_secret_value())
    return TokenResponse(
        access_token=token.access_token,
        token_type=token.token_type,
        expires_in=token.expires_in,
    )


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
async def recovery_options(request: Request) -> RecoveryOptionsResponse:
    settings = cast(Settings, request.app.state.settings)
    return RecoveryOptionsResponse(email_reset=settings.email_reset_enabled)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_reset(
    body: ResetRequest,
    request: Request,
    service: Recovery,
    background: BackgroundTasks,
) -> dict[str, str]:
    email = await service.request_reset(str(body.email))
    mailer = cast(SmtpMailer | None, request.app.state.mailer)
    if email is not None and mailer is not None:
        background.add_task(send_quietly, mailer, email)
    # Same answer whether or not the account exists.
    return {"status": "accepted"}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_reset(body: ResetConfirmRequest, service: Recovery) -> Response:
    await service.confirm_reset(body.token, body.new_password.get_secret_value())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/recovery-code", response_model=RecoveryCodeResponse)
async def use_recovery_code(
    body: RecoveryCodeRequest, service: Recovery
) -> RecoveryCodeResponse:
    password = body.new_password.get_secret_value() if body.new_password else None
    return RecoveryCodeResponse(email=await service.redeem_code(body.code, password))


@router.get("/recovery-codes", response_model=RecoveryStatusResponse)
async def recovery_status(
    user: AuthenticatedUser, service: Recovery
) -> RecoveryStatusResponse:
    return RecoveryStatusResponse(remaining=await service.remaining_codes(user.id))


@router.post(
    "/recovery-codes",
    response_model=RecoveryCodesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def new_recovery_codes(
    body: PasswordConfirmation, user: AuthenticatedUser, service: Recovery
) -> RecoveryCodesResponse:
    codes = await service.regenerate_codes(user, body.password.get_secret_value())
    return RecoveryCodesResponse(codes=codes)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest, user: AuthenticatedUser, service: Recovery
) -> Response:
    await service.change_password(
        user,
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
