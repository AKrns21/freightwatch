"""Standardized error handling decorator for consistent error management."""

import functools
import inspect
import traceback
from collections.abc import Callable
from typing import Any

from app.utils.logger import get_logger


def handle_service_errors(
    operation_name: str,
    logger_name: str | None = None,
    reraise: bool = True,
    default_return: Any = None,
):
    """Decorator for consistent error handling in service methods.

    Args:
        operation_name: Human-readable operation name for logging (e.g., "tariff_lookup").
        logger_name: Optional logger name override (defaults to decorated function's module).
        reraise: Whether to re-raise the exception after logging (default: True).
        default_return: Value to return if exception occurs and reraise=False (default: None).

    Usage:
        @handle_service_errors("tariff_lookup")
        async def lookup_tariff(self, shipment_id: str):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = get_logger(logger_name or func.__module__)
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                context: dict[str, Any] = {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "function": func.__name__,
                }
                if kwargs:
                    safe_kwargs = {
                        k: v
                        for k, v in list(kwargs.items())[:3]
                        if k not in {"password", "api_key", "token", "secret"}
                    }
                    context.update(safe_kwargs)
                logger.error(f"{operation_name}_failed", **context, exc_info=False)
                logger.debug(f"{operation_name}_stack_trace", stack_trace=traceback.format_exc())
                if reraise:
                    raise
                logger.warning(
                    f"{operation_name}_using_default_return", default_return=default_return
                )
                return default_return

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = get_logger(logger_name or func.__module__)
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context: dict[str, Any] = {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "function": func.__name__,
                }
                if kwargs:
                    safe_kwargs = {
                        k: v
                        for k, v in list(kwargs.items())[:3]
                        if k not in {"password", "api_key", "token", "secret"}
                    }
                    context.update(safe_kwargs)
                logger.error(f"{operation_name}_failed", **context, exc_info=False)
                logger.debug(f"{operation_name}_stack_trace", stack_trace=traceback.format_exc())
                if reraise:
                    raise
                logger.warning(
                    f"{operation_name}_using_default_return", default_return=default_return
                )
                return default_return

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
