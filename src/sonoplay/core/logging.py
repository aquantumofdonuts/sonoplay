"""Centralized logging configuration for SonoPlay."""

import logging
import re
import sys


_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
_TOKEN_IN_URL = re.compile(r"""(\bX-Plex-Token=)[^&\s"'<>]+""", re.IGNORECASE)
_TOKEN_IN_MAPPING = re.compile(r"""(\bX-Plex-Token["']\s*:\s*)(["'])[^"']*\2""", re.IGNORECASE)


class _RedactingFormatter(logging.Formatter):
    def __init__(self, formatter: logging.Formatter | None):
        super().__init__()
        self.formatter = formatter or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        message = self.formatter.format(record)
        message = _TOKEN_IN_URL.sub(r"\g<1>[redacted]", message)
        return _TOKEN_IN_MAPPING.sub(r"\g<1>\g<2>[redacted]\g<2>", message)


def apply_log_level(level: str) -> None:
    # Uvicorn configures its own handlers before application startup. Preserve
    # their output formats while redacting credentials in messages and traces.
    root = logging.getLogger()
    for logger in (root, logging.getLogger("uvicorn"), logging.getLogger("uvicorn.access")):
        for handler in logger.handlers:
            if not isinstance(handler.formatter, _RedactingFormatter):
                handler.setFormatter(_RedactingFormatter(handler.formatter))

    configured_level = _LEVELS.get(level.strip().upper())
    root.setLevel(logging.INFO if configured_level is None else configured_level)
    if configured_level is None:
        logging.getLogger(__name__).warning("Unrecognized LOG_LEVEL, using INFO")


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger with SonoPlay's default format."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    apply_log_level(level)

    # Reduce noise from chatty libraries.
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


__all__ = ["configure_logging"]
