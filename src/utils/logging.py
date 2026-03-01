"""Structured logging configuration per H7 and H8.

Configures structlog with:
- JSON renderer in production, console renderer for development
- Secret redaction processor per H8
- Standard context fields
"""

import logging
import re
import sys

import structlog


# Patterns that look like secrets — redact their values in log output
_SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|apikey|password|passwd|secret|token|authorization|bearer)"
    r"",
    re.IGNORECASE,
)

# Keys in event dicts whose values should be redacted
_SECRET_KEYS = frozenset({
    "api_key", "apikey", "api-key",
    "password", "passwd",
    "secret", "secret_key",
    "token", "access_token", "refresh_token",
    "authorization", "bearer",
})


def _redact_secrets(
    logger: logging.Logger, method_name: str, event_dict: dict
) -> dict:
    """Structlog processor that redacts secret-looking values per H8."""
    for key in list(event_dict.keys()):
        if key in _SECRET_KEYS:
            event_dict[key] = "***"
        elif isinstance(event_dict[key], str) and _SECRET_PATTERNS.search(key):
            event_dict[key] = "***"
    return event_dict


def configure_logging(log_level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog for the application.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, use JSON renderer. Otherwise use console renderer.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging to use structlog formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.WARNING)
