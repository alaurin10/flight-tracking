"""Logging: one JSON object per line for the long-running process, plain text otherwise."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "t": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class StderrHandler(logging.StreamHandler[TextIO]):
    """Writes to whatever `sys.stderr` is at the moment of writing, so the handler
    keeps working when something (a test, a daemon) swaps the stream."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr  # not setStream(): that flushes the old, maybe closed, stream
        super().emit(record)


def setup_logging(level: int = logging.INFO, *, json_lines: bool = True) -> None:
    """JSON lines for the worker and the API; the bare message for
    commands a person runs in a terminal."""
    handler = StderrHandler()
    handler.setFormatter(JsonFormatter() if json_lines else logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
