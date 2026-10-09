"""
HOOD Structured Logging Engine
Produces structured, redacted JSON and console logs.
"""

import sys
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pathlib import Path
from .redactor import sanitize_object, redact_string


class HoodStructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_string(record.getMessage()),
        }
        if hasattr(record, "task_id") and record.task_id:
            data["task_id"] = record.task_id
        if hasattr(record, "actor") and record.actor:
            data["actor"] = record.actor
        if hasattr(record, "correlation_id") and record.correlation_id:
            data["correlation_id"] = record.correlation_id
        if hasattr(record, "extra_payload") and record.extra_payload:
            data["payload"] = sanitize_object(record.extra_payload)
        return json.dumps(data)


def get_logger(name: str = "hood", log_file: Optional[Path] = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        # Console handler with structured formatting
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(HoodStructuredFormatter())
        logger.addHandler(ch)

        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(HoodStructuredFormatter())
            logger.addHandler(fh)
    return logger
