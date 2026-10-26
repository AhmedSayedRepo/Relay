from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from relay.application.agent import AgentService
from relay.application.errors import AuthenticationFailed
from relay.domain.agent import DevicePrincipal
from relay.infrastructure.config import Settings
from relay.infrastructure.device_security import DeviceTokens
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import bearer, unit_of_work


def agent_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> AgentService:
    settings = cast(Settings, request.app.state.settings)
    return AgentService(
        uow,
        cast(DeviceTokens, request.app.state.device_tokens),
        challenge_minutes=settings.enrollment_expire_minutes,
        refresh_days=settings.device_refresh_expire_days,
        coalesce_seconds=settings.device_contact_coalesce_seconds,
    )


Service = Annotated[AgentService, Depends(agent_service)]


async def current_device(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    service: Service,
) -> DevicePrincipal:
    if credentials is None or len(credentials.credentials) > 4096:
        raise AuthenticationFailed("Device authentication required.")
    return await service.authenticate(credentials.credentials)


AuthenticatedDevice = Annotated[DevicePrincipal, Depends(current_device)]
