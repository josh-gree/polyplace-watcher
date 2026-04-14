from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from pythonjsonlogger.json import JsonFormatter

DEFAULT_LOG_FILE = "logs/polyplace-watcher.log"
LOGGER_NAME = "polyplace_watcher"

_HANDLER_MARKER = "_polyplace_watcher_handler"


def _json_default(value: object) -> str:
    return str(value)


def scrub_url(url: str) -> str:
    """Strip userinfo and query string from a URL before logging."""
    try:
        parsed = urlparse(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc, query=""))
    except Exception:
        return "<url>"


class ProjectJsonFormatter(JsonFormatter):
    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: Mapping[str, Any],
    ) -> None:
        super().add_fields(log_data, record, message_dict)
        log_data["timestamp"] = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        log_data["level"] = log_data.pop("levelname", record.levelname)
        log_data["logger"] = log_data.pop("name", record.name)


def _remove_polyplace_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()


def _project_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure_logging() -> None:
    """Configure project logging as JSON lines on stdout and a local file."""
    project_logger = _project_logger()
    project_logger.setLevel(logging.DEBUG)
    project_logger.propagate = False

    _remove_polyplace_handlers(project_logger)

    formatter = ProjectJsonFormatter(
        "{message}{levelname}{name}",
        style="{",
        json_default=_json_default,
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.setFormatter(formatter)
    setattr(stdout_handler, _HANDLER_MARKER, True)
    project_logger.addHandler(stdout_handler)

    log_path = Path(DEFAULT_LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_MARKER, True)
    project_logger.addHandler(file_handler)


def reset_logging() -> None:
    """Remove project-owned handlers.

    This is mainly useful for tests and embedded runtimes that need to rebuild handlers.
    """
    project_logger = _project_logger()
    _remove_polyplace_handlers(project_logger)
    project_logger.propagate = True
