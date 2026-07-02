"""Logging configuration for OpenAQ extractor."""

import logging
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_logging_configured = False


def setup_logging(log_file: Path, debug: bool = False) -> None:
    """
    Configure logging to file and stderr.
    Avoids duplicate handlers if called multiple times (e.g. in tests).
    """
    global _logging_configured
    if _logging_configured:
        return

    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if debug else logging.WARNING
    formatter = logging.Formatter(LOG_FORMAT)

    logger = logging.getLogger("openaq_extractor")
    logger.setLevel(level)

    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _logging_configured = True
