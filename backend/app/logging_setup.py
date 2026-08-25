"""Rotating file logging, configured once at process start."""
import logging
from logging.handlers import RotatingFileHandler

from app.config import settings


def configure_logging() -> None:
    logger = logging.getLogger("reliance_swing")
    if logger.handlers:
        return  # already configured (e.g. reloader re-import)

    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        settings.logs_dir / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)
