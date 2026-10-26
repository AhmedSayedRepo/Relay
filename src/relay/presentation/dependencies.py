from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from relay.application.auth import AuthService
from relay.application.devices import DeviceService
from relay.application.errors import AuthenticationFailed
from relay.domain.users import User
from relay.infrastructure.database import Database
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.security import Argon2Passwords, JwtTokens

bearer = HTTPBearer(auto_error=False)


async def unit_of_work(request: Request) -> AsyncIterator[SqlUnitOfWork]:
    database = cast(Database, request.app.state.database)
    async with database.session() as session:
        yield SqlUnitOfWork(session)


def auth_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> AuthService:
    return AuthService(
        uow,
        cast(Argon2Passwords, request.app.state.passwords),
        cast(JwtTokens, request.app.state.tokens),
    )


def device_service(
    uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)],
) -> DeviceService:
    return DeviceService(uow)


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    service: Annotated[AuthService, Depends(auth_service)],
) -> User:
    if credentials is None or len(credentials.credentials) > 4096:
        raise AuthenticationFailed("Authentication required.")
    return await service.current_user(credentials.credentials)


AuthenticatedUser = Annotated[User, Depends(current_user)]
