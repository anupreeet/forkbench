"""Structured logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

# Runta API tokens start with "rt"; mask anything token-shaped plus the exact configured value.
_TOKEN_SHAPE = re.compile(r"\brt[A-Za-z0-9_\-]{16,}")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return _TOKEN_SHAPE.sub("rt***", text)


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        # Format first, then redact the final text: converting args to strings before formatting
        # would break placeholders like %d / %.2f.
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return redact(json.dumps(payload, default=str))


def setup_logging(log_path: str | None, secrets: tuple[str, ...], verbose: bool = False) -> None:
    root = logging.getLogger("forkbench")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    handlers: list[logging.Handler] = [console]
    if log_path:
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(JsonFormatter())
        handlers.append(file_handler)
    for handler in handlers:
        handler.addFilter(RedactingFilter(secrets))
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"forkbench.{name}")
