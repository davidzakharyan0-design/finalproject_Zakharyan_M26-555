"""JSON Lines, UTC, INFO и ротация actions.log по размеру."""

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock

from valutatrade_hub.infra.settings import SettingsLoader

_LOCK = RLock()
_SIGNATURE = None


class ActionFormatter(logging.Formatter):
    """Одна JSON-запись на строку, без traceback и произвольных аргументов."""

    def format(self, record):
        """Сериализует событие с временем UTC в одну строку JSON."""
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            **record.action_data,
        }
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)


def configure_logging():
    """Настраивает только журнал приложения; повторные вызовы не дублируют записи."""
    global _SIGNATURE
    settings = SettingsLoader()
    signature = tuple(
        settings.get(key)
        for key in ("log_file", "log_max_bytes", "log_backup_count", "log_level")
    )
    logger = logging.getLogger("valutatrade.actions")
    with _LOCK:
        if signature != _SIGNATURE or not logger.handlers:
            path, size, count, level = signature
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                path, maxBytes=size, backupCount=count, encoding="utf-8"
            )
            handler.setFormatter(ActionFormatter())
            for old in logger.handlers[:]:
                logger.removeHandler(old)
                old.close()
            logger.addHandler(handler)
            logger.setLevel(level)
            logger.propagate = False
            _SIGNATURE = signature
    return logger
