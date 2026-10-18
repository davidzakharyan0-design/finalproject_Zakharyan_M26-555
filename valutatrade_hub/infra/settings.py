"""Единая конфигурация из [tool.valutatrade] в pyproject.toml."""

import tomllib
from pathlib import Path
from threading import RLock

DEFAULTS = {
    "data_dir": "data",
    "users_file": "users.json",
    "portfolios_file": "portfolios.json",
    "rates_file": "rates.json",
    "rates_ttl_seconds": 300,
    "default_base_currency": "USD",
    "log_file": "logs/actions.log",
    "log_level": "INFO",
    "log_format": "json",
    "log_max_bytes": 1048576,
    "log_backup_count": 3,
}


class SettingsLoader:
    """Singleton через __new__: явно и без дополнительной метаклассовой магии."""

    _instance = None
    _lock = RLock()

    def __new__(cls, config_path=None):
        """Создаёт и загружает единственный экземпляр при первом обращении."""
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._path = Path(config_path or "pyproject.toml").resolve()
                instance.reload()
                cls._instance = instance
            elif config_path is not None:
                if Path(config_path).resolve() != cls._instance._path:
                    raise ValueError("SettingsLoader уже использует другой файл")
            return cls._instance

    def get(self, key, default=None):
        """Возвращает значение ключа или переданный default."""
        return self._values.get(key, default)

    def reload(self):
        """Атомарно заменяет конфигурацию после полной проверки новых значений."""
        values = dict(DEFAULTS)
        if self._path.exists():
            with self._path.open("rb") as file:
                section = tomllib.load(file).get("tool", {}).get("valutatrade", {})
            if not isinstance(section, dict):
                raise ValueError("tool.valutatrade должен быть таблицей TOML")
            if section.keys() - DEFAULTS.keys():
                raise ValueError("Неизвестный ключ tool.valutatrade")
            values.update(section)
        for key in ("rates_ttl_seconds", "log_max_bytes", "log_backup_count"):
            if type(values[key]) is not int or values[key] <= 0:
                raise ValueError(f"{key} должен быть положительным целым")
        for key in ("data_dir", "log_file"):
            if not isinstance(values[key], str) or not values[key].strip():
                raise ValueError(f"{key} должен быть непустым путём")
            path = Path(values[key]).expanduser()
            if not path.is_absolute():
                path = self._path.parent / path
            values[key] = str(path.resolve())
        for key in ("users_file", "portfolios_file", "rates_file"):
            value = values[key]
            if (
                not isinstance(value, str)
                or Path(value).name != value
                or not value.endswith(".json")
            ):
                raise ValueError(f"{key} должен быть именем JSON-файла без пути")
        if (
            len({values[k] for k in ("users_file", "portfolios_file", "rates_file")})
            != 3
        ):
            raise ValueError("Имена JSON-файлов должны различаться")
        if values["log_format"] != "json":
            raise ValueError("Поддерживается только log_format = 'json'")
        if values["log_level"] not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("Неизвестный уровень логирования")
        from valutatrade_hub.core.currencies import get_currency

        values["default_base_currency"] = get_currency(
            values["default_base_currency"]
        ).code
        with self._lock:
            self._values = values
