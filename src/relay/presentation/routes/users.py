"""Admin-only user management (role checks live in ``current_user``)."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field, SecretStr

from relay.application.users import UserAdminService
from relay.domain.users import Role
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.security import Argon2Passwords
from relay.presentation.dependencies import (
    AuthenticatedUser,
    current_user,
    unit_of_work,
)
from relay.presentation.schemas import InputModel, UserResponse

router = APIRouter(
    prefix="/users", tags=["Users"], dependencies=[Depends(current_user)]
)


def user_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> UserAdminService:
    return UserAdminService(uow, cast(Argon2Passwords, request.app.state.passwords))


Service = Annotated[UserAdminService, Depends(user_service)]


class UserListResponse(BaseModel):
    items: list[UserResponse]


class UserCreateRequest(InputModel):
    email: EmailStr = Field(max_length=254)
    role: Role
    password: SecretStr = Field(min_length=12, max_length=1024)


class UserUpdateRequest(InputModel):
    role: Role | None = None
    is_active: bool | None = None
    password: SecretStr | None = Field(default=None, min_length=12, max_length=1024)


class UserDeleteResponse(BaseModel):
    removed: bool  # False: kept hidden because the user has review history


@router.get("", response_model=UserListResponse)
async def list_users(service: Service) -> UserListResponse:
    return UserListResponse(
        items=[UserResponse.model_validate(user) for user in await service.list()]
    )


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(body: UserCreateRequest, service: Service) -> UserResponse:
    user = await service.create(
        str(body.email), body.role, body.password.get_secret_value()
    )
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID, body: UserUpdateRequest, actor: AuthenticatedUser, service: Service
) -> UserResponse:
    user = await service.update(
        actor,
        user_id,
        role=body.role,
        is_active=body.is_active,
        password=body.password.get_secret_value() if body.password else None,
    )
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", response_model=UserDeleteResponse)
async def delete_user(
    user_id: UUID, actor: AuthenticatedUser, service: Service
) -> UserDeleteResponse:
    return UserDeleteResponse(removed=await service.delete(actor, user_id))
