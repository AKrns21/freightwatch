"""Structured logging setup using structlog."""

import logging
import sys
from contextvars import ContextVar
from typing import Any, TextIO

import structlog

from app.config import settings

# Context variables for request tracking
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


class SafeStreamHandler(logging.StreamHandler):
    """Stream handler that handles BrokenPipeError gracefully."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except BrokenPipeError:
            pass
        except Exception:
            self.handleError(record)


class SafePrintLogger:
    """Print logger that handles BrokenPipeError gracefully."""

    def __init__(self, file: TextIO = None):
        self._file = file or sys.stdout

    def msg(self, message: str) -> None:
        try:
            print(message, file=self._file, flush=True)
        except BrokenPipeError:
            pass
        except Exception:
            try:
                print(f"Logging error: {message}", file=sys.stderr)
            except Exception:
                pass

    def debug(self, message: str) -> None:
        self.msg(message)

    def info(self, message: str) -> None:
        self.msg(message)

    def warning(self, message: str) -> None:
        self.msg(message)

    def error(self, message: str) -> None:
        self.msg(message)

    def critical(self, message: str) -> None:
        self.msg(message)

    def __call__(self) -> "SafePrintLogger":
        return self


class SafePrintLoggerFactory:
    """Factory for creating SafePrintLogger instances."""

    def __init__(self, file: TextIO = None):
        self._file = file

    def __call__(self, *args: Any) -> SafePrintLogger:
        return SafePrintLogger(file=self._file)


def add_request_context(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Add request context (request_id, tenant_id, user_id) to log entries."""
    request_id = request_id_var.get()
    tenant_id = tenant_id_var.get()
    user_id = user_id_var.get()

    if request_id:
        event_dict["request_id"] = request_id
    if tenant_id:
        event_dict["tenant_id"] = tenant_id
    if user_id:
        event_dict["user_id"] = user_id

    return event_dict


def setup_logging() -> None:
    """Configure structured logging for the application."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper()))

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = SafeStreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_request_context,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            (
                structlog.processors.JSONRenderer()
                if settings.log_format == "json"
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=SafePrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__) -> Any:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def set_tenant_id(tenant_id: str) -> None:
    tenant_id_var.set(tenant_id)


def set_user_id(user_id: str) -> None:
    user_id_var.set(user_id)


def clear_request_context() -> None:
    request_id_var.set(None)
    tenant_id_var.set(None)
    user_id_var.set(None)
