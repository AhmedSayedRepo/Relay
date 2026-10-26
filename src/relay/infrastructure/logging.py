import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from relay.infrastructure.config import Settings

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
SAFE_EVENTS = frozenset(
    {
        "request.completed",
        "request.failed",
        "readiness.failed",
        "application.started",
        "application.stopped",
        "events.batch_processed",
        "events.device_deleted",
        "events.retention_purged",
    }
)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        trusted = (
            record.name.startswith("relay.")
            and isinstance(record.msg, str)
            and record.msg in SAFE_EVENTS
        )
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "environment": self.environment,
            "request_id": request_id_context.get(),
            "message": record.msg if trusted else "external.log.suppressed",
        }
        if trusted:
            for key in ("method", "route", "status_code", "duration_ms"):
                if hasattr(record, key):
                    payload[key] = getattr(record, key)
            for key in (
                "accepted_count",
                "duplicate_count",
                "rejected_count",
                "count",
            ):
                value = getattr(record, key, None)
                if type(value) is int and value >= 0:
                    payload[key] = value
            device_id = getattr(record, "device_id", None)
            if isinstance(device_id, str) and len(device_id) <= 64:
                payload["device_id"] = device_id
        # Deliberately omit arbitrary messages/args/extras, exception text, and traces.
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings.app_name, settings.app_env))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    for name in ("sqlalchemy.engine", "sqlalchemy.pool", "uvicorn.access", "httpx"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.WARNING)
