from typing import Annotated, cast

from fastapi import Depends, Request

from relay.application.operations import OperationsService
from relay.infrastructure.config import Settings
from relay.infrastructure.excel_export import OpenpyxlWorkbookExporter
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import unit_of_work


def operations_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> OperationsService:
    settings = cast(Settings, request.app.state.settings)
    return OperationsService(
        uow,
        OpenpyxlWorkbookExporter(),
        audit_retention_days=settings.operator_audit_retention_days,
    )


Service = Annotated[OperationsService, Depends(operations_service)]
