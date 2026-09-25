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


def _secret_named(name: str) -> bool:
    """A secret-shaped field name: contains one of the substrings, or is
    `key` / ends in `_key`. `key` is matched as a whole word, not a substring:
    `keys` (a list of env var names) and `key_path` (a file path) are
    diagnostics worth keeping."""
    lowered = name.lower()
    if lowered == "key" or lowered.endswith("_key"):
        return True
    return any(token in lowered for token in _REDACT_SUBSTRINGS)


def _scrub(value: Any) -> Any:
    """Redact secret-named entries at any depth of nested dicts and lists.

    Builds copies rather than editing in place: the containers belong to the
    caller, which may still be using them after the log call returns.
    """
    if isinstance(value, dict):
        return {k: _REDACTED if _secret_named(str(k)) else _scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item) for item in value]
    return value


def _redact(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Mask values whose field name looks secret-shaped, at any depth, before
    they are written."""
    for key, value in event_dict.items():
        event_dict[key] = _REDACTED if _secret_named(key) else _scrub(value)
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
