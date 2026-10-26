from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from relay.application.errors import AuthenticationFailed, Conflict, NotFound
from relay.domain.errors import DomainError
from relay.infrastructure.logging import request_id_context


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id_context.get(),
            }
        },
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationFailed)
    async def auth_error(request: Request, exc: AuthenticationFailed) -> JSONResponse:
        return error_response(
            401, "unauthorized", str(exc), {"WWW-Authenticate": "Bearer"}
        )

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> JSONResponse:
        return error_response(404, "not_found", str(exc))

    @app.exception_handler(Conflict)
    async def conflict(request: Request, exc: Conflict) -> JSONResponse:
        return error_response(409, "conflict", str(exc))

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(422, "validation_error", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's default detail may echo passwords and other raw input.
        return error_response(422, "validation_error", "Request validation failed.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        messages = {
            400: "Bad request.",
            401: "Authentication required.",
            403: "Access forbidden.",
            404: "Resource not found.",
            405: "Method not allowed.",
            413: "Request body too large.",
        }
        return error_response(
            exc.status_code,
            f"http_{exc.status_code}",
            messages.get(exc.status_code, "Request failed."),
            dict(exc.headers) if exc.headers else None,
        )
