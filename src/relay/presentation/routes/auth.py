from typing import Annotated

from fastapi import APIRouter, Depends

from relay.application.auth import AuthService
from relay.presentation.dependencies import AuthenticatedUser, auth_service
from relay.presentation.schemas import LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


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


@router.get("/me", response_model=UserResponse)
async def me(user: AuthenticatedUser) -> UserResponse:
    return UserResponse.model_validate(user)
