"""Structured logging with redaction at the serialization boundary."""

import json
import logging
import re
from typing import Any

SENSITIVE = re.compile(r"authorization|cookie|token|secret|password|credential", re.I)
TOKEN = re.compile(r"cf_(?:login|session|key)_[A-Za-z0-9_-]+|Bearer\s+\S+", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[redacted]" if SENSITIVE.search(str(k)) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return TOKEN.sub("[redacted]", value)
    return value


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        if record.exc_info and record.exc_info[0]:
            data["exception"] = record.exc_info[0].__name__
        return json.dumps(redact(data))
