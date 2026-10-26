import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from relay.infrastructure.logging import request_id_context
from relay.presentation.errors import error_response

logger = logging.getLogger(__name__)
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", flags=re.ASCII)
CHUNK_PATH = re.compile(
    r"/api/v1/device/objects/uploads/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/chunks",
    flags=re.ASCII,
)


class JsonCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code == 400:
            headers = {
                key: value
                for key, value in response.headers.items()
                if key not in {"content-length", "content-type"}
            }
            return error_response(
                400, "cors_rejected", "Cross-origin request rejected.", headers
            )
        return response


class RequestMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = 16384,
        max_chunk_bytes: int = 256 * 1024,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_chunk_bytes = max_chunk_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = Headers(scope=scope).getlist("x-request-id")
        request_id = (
            supplied[0]
            if len(supplied) == 1 and SAFE_ID.fullmatch(supplied[0])
            else str(uuid4())
        )
        context_token = request_id_context.set(request_id)
        started = time.monotonic()
        status_code = 500
        response_started = False
        path = str(scope.get("path", ""))

        async def correlated_send(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=()"
                )
                if path == "/operator" or path.startswith("/operator/"):
                    headers["Content-Security-Policy"] = (
                        "default-src 'self'; base-uri 'none'; object-src 'none'; "
                        "frame-ancestors 'none'; form-action 'self'; "
                        "script-src 'self'; style-src 'self'; "
                        "img-src 'self' blob:; connect-src 'self'"
                    )
            await send(message)

        try:
            chunk_request = (
                scope["method"] == "PUT" and CHUNK_PATH.fullmatch(path) is not None
            )
            body_limit = self.max_chunk_bytes if chunk_request else self.max_body_bytes
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > body_limit:
                    await error_response(
                        413, "body_too_large", "Request body too large."
                    )(scope, receive, correlated_send)
                    return
                if not message.get("more_body", False):
                    break
            delivered = False

            async def buffered_receive() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            try:
                await self.app(scope, buffered_receive, correlated_send)
            except Exception:
                logger.error("request.failed")
                if response_started:
                    raise
                await error_response(
                    500, "internal_error", "An internal error occurred."
                )(scope, receive, correlated_send)
        finally:
            route = scope.get("route")
            method = scope["method"]
            logger.info(
                "request.completed",
                extra={
                    "method": (
                        method
                        if method
                        in {"GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS", "HEAD"}
                        else "OTHER"
                    ),
                    "route": getattr(route, "path", "unmatched"),
                    "status_code": status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
            )
            request_id_context.reset(context_token)


class HostValidationMiddleware:
    def __init__(self, app: ASGIApp, allowed_hosts: list[str]) -> None:
        self.app = app
        self.allowed_hosts = frozenset(allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            hosts = Headers(scope=scope).getlist("host")
            if (
                len(hosts) != 1
                or hosts[0].split(":")[0].lower() not in self.allowed_hosts
            ):
                await error_response(400, "invalid_host", "Invalid host header.")(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)
