"""Application logging setup for IBIS Auto Downloader."""

from __future__ import annotations

import logging
import io
import sys
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


_HANDLER_MARKER = "_ibis_application_handler"


class _RunIdFilter(logging.Filter):
    """Attach the application run identifier to every emitted record."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Use ``io.open`` so application logging is resilient to patched builtins."""

    def _open(self):
        return io.open(
            self.baseFilename,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
        )


def configure_logging(*, run_id: str | None = None, log_dir: str | Path | None = None) -> str:
    """Configure console and rotating-file logging, returning the run ID.

    Repeated calls reuse the active configuration so scheduled invocations do
    not duplicate console output or create multiple handlers.
    """
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return handler.run_id

    if log_dir is None:
        from config import LOG_DIR

        log_dir = LOG_DIR
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    record_filter = _RunIdFilter(run_id)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [run_id=%(run_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    file_handler = _SafeRotatingFileHandler(
        log_dir / "ibis-auto-downloader.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    for handler in (console_handler, file_handler):
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        handler.addFilter(record_filter)
        setattr(handler, _HANDLER_MARKER, True)
        handler.run_id = run_id
        root_logger.addHandler(handler)

    root_logger.setLevel(logging.INFO)
    return run_id


def log(message: str) -> None:
    """Compatibility helper for legacy callers."""
    logging.getLogger("ibis").info(message)
