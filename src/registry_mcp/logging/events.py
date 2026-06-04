"""structlog configuration: JSON event log to stderr and a file.

Logs go to stderr (not stdout) so they never corrupt the JSON-RPC stream when
the server runs over the stdio transport.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from registry_mcp.config import Settings

_REDACT_SUBSTRINGS = ("token", "password", "secret", "authorization", "api_key", "apikey")
_REDACTED = "***redacted***"


def _redact(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Mask values whose key name looks secret-shaped before they are written."""
    for key in event_dict:
        if any(token in key.lower() for token in _REDACT_SUBSTRINGS):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging to emit JSON to stderr and a log file."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if settings.registry_log_path:
        log_path = Path(settings.registry_log_path).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
