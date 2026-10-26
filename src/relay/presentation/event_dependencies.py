from typing import Annotated, cast

from fastapi import Depends, Request

from relay.application.events import EventService
from relay.infrastructure.config import Settings
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import unit_of_work


def event_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> EventService:
    settings = cast(Settings, request.app.state.settings)
    return EventService(uow, retention_days=settings.raw_event_retention_days)


Service = Annotated[EventService, Depends(event_service)]
