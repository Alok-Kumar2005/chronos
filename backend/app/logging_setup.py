"""Structured JSON logging configuration."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.config import settings


class JSONFormatter(logging.Formatter):
    """Format log records as JSON with structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a JSON string."""
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields (but filter out sensitive data)
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id

        if hasattr(record, "universe_id"):
            log_data["universe_id"] = record.universe_id

        # Never log these sensitive fields
        for sensitive_key in ["password", "secret", "token", "api_key", "authorization"]:
            if hasattr(record, sensitive_key):
                delattr(record, sensitive_key)

        return json.dumps(log_data)


def setup_logging() -> None:
    """Configure structured JSON logging for the application."""
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO if settings.ENV == "prod" else logging.DEBUG)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler with JSON formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
