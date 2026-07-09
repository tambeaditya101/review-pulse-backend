"""Structured logging setup for Review Pulse."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

from review_pulse.config import PROJECT_ROOT

_LOG_DIR = PROJECT_ROOT / "data" / "logs"


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON strings for structured stdout logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Include context info if present in record extras
        if hasattr(record, "run_id"):
            log_data["run_id"] = record.run_id
        if hasattr(record, "node"):
            log_data["node"] = record.node
            
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_data)


def setup_logging(
    json_stdout: bool = True,
    file_logging: bool = True,
    log_level: int = logging.INFO,
) -> None:
    """Configure system-wide logging with JSON stdout formatting and file rotating logging."""
    root_logger = logging.getLogger()
    
    # Clear existing handlers
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    # 1. Console Handler (JSON formatting)
    console_handler = logging.StreamHandler()
    if json_stdout:
        console_handler.setFormatter(JsonFormatter())
    else:
        # Standard clean human-readable formatter
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
        )
    root_logger.addHandler(console_handler)

    # 2. File Handler (Rotating file logging)
    if file_logging:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file_path = _LOG_DIR / "review-pulse.log"
        
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
        )
        root_logger.addHandler(file_handler)

    # Silence noisy dependencies
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
