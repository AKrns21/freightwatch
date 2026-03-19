"""Structured logging setup using structlog.

Writes to two sinks:
- stdout  (always)
- rotating file at settings.log_file (default: logs/freightwatch.log)

Rotation: 10 MB per file, 7 backups kept → ~70 MB total cap.
Both sinks receive the same formatted output (JSON in production,
console-coloured in development).
"""

import logging
import logging.handlers
import sys
from contextvars import ContextVar
from pathlib import Path
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
    """Print logger that writes to stdout and optionally a file handler."""

    def __init__(
        self,
        file: TextIO | None = None,
        file_handler: logging.Handler | None = None,
    ) -> None:
        self._file = file or sys.stdout
        self._file_handler = file_handler

    def msg(self, message: str) -> None:
        # Write to stdout
        try:
            print(message, file=self._file, flush=True)
        except BrokenPipeError:
            pass
        except Exception:
            try:
                print(f"Logging error: {message}", file=sys.stderr)
            except Exception:
                pass

        # Mirror to rotating file
        if self._file_handler is not None:
            record = logging.LogRecord(
                name="structlog",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=message,
                args=(),
                exc_info=None,
            )
            try:
                self._file_handler.emit(record)
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

    def __init__(
        self,
        file: TextIO | None = None,
        file_handler: logging.Handler | None = None,
    ) -> None:
        self._file = file
        self._file_handler = file_handler

    def __call__(self, *args: Any) -> SafePrintLogger:
        return SafePrintLogger(file=self._file, file_handler=self._file_handler)


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


def _make_file_handler() -> logging.handlers.RotatingFileHandler | None:
    """Create a RotatingFileHandler for settings.log_file, or None on error."""
    try:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            filename=str(log_path),
            maxBytes=settings.log_file_max_mb * 1024 * 1024,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        return handler
    except Exception as exc:
        # Don't crash startup if we can't open the log file
        print(f"WARNING: could not open log file {settings.log_file!r}: {exc}", file=sys.stderr)
        return None


def setup_logging() -> None:
    """Configure structured logging for the application."""
    log_level = getattr(logging, settings.log_level.upper())

    # Build rotating file handler
    file_handler = _make_file_handler()

    # Configure root (stdlib) logger — captures uvicorn, SQLAlchemy, etc.
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    stream_handler = SafeStreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(stream_handler)

    if file_handler is not None:
        root_logger.addHandler(file_handler)

    # Renderer: JSON in production, coloured console in development
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_request_context,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.ExceptionRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=SafePrintLoggerFactory(file=sys.stdout, file_handler=file_handler),
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
