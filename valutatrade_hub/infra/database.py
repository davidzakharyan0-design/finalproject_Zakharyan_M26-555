"""Singleton над JSON и блокировка операций в пределах процесса."""

from contextlib import contextmanager, nullcontext
from functools import wraps
from threading import RLock

from valutatrade_hub.core.constants import PORTFOLIOS_FILE, RATES_FILE, USERS_FILE
from valutatrade_hub.core.utils import JsonStorage
from valutatrade_hub.infra.settings import SettingsLoader


class DatabaseManager:
    """__new__ проще метакласса; повторный вызов не создаёт новое хранилище."""

    _instance = None
    _lock = RLock()

    def __new__(cls):
        """Инициализирует лениво, без побочных эффектов при импорте."""
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                settings = SettingsLoader()
                filenames = {
                    USERS_FILE: settings.get("users_file"),
                    PORTFOLIOS_FILE: settings.get("portfolios_file"),
                    RATES_FILE: settings.get("rates_file"),
                }
                instance._storage = JsonStorage(settings.get("data_dir"), filenames)
                cls._instance = instance
            return cls._instance

    @property
    def data_dir(self):
        """Возвращает каталог текущего хранилища."""
        return self._storage.data_dir

    def load(self, filename):
        """Читает документ под блокировкой процесса."""
        with self._lock:
            return self._storage.load(filename)

    def save(self, filename, data):
        """Атомарно заменяет один документ под блокировкой процесса."""
        with self._lock:
            self._storage.save(filename, data)

    @contextmanager
    def transaction(self):
        """Сериализует чтение–изменение–запись; не является дисковой транзакцией."""
        with self._lock:
            yield self


def storage_transaction(function):
    """Защищает весь сценарий; позволяет внедрять изолированное тестовое хранилище."""

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        """Выполняет сценарий внутри блокировки выбранного хранилища."""
        transaction = getattr(self.storage, "transaction", nullcontext)
        with transaction():
            return function(self, *args, **kwargs)

    return wrapped
