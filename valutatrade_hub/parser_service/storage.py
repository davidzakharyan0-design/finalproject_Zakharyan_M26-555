"""История измерений и снимок: атомарная замена и блокировка писателей Unix."""

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.utils import atomic_write_json, utc_now, validate_number
from valutatrade_hub.parser_service.config import ParserConfig


def utc_timestamp(value):
    """Нормализует ISO-время с часовым поясом для сравнения и уникального ID."""
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Ожидалось время с часовым поясом")
    result = result.astimezone(timezone.utc)
    if result > utc_now():
        raise ValueError("Измерение датировано будущим")
    return result.isoformat().replace("+00:00", "Z")


def validate_record(record):
    """Проверяет измерение до любой записи на диск."""
    source = get_currency(record["from_currency"]).code
    target = get_currency(record["to_currency"]).code
    if source != record["from_currency"] or target != record["to_currency"]:
        raise ValueError("Коды измерений должны быть в верхнем регистре")
    stamp = utc_timestamp(record["timestamp"])
    identifier = f"{source}_{target}_{stamp}"
    if record["id"] != identifier:
        raise ValueError("ID измерения не соответствует паре и времени")
    if record["source"] not in {"CoinGecko", "ExchangeRate-API"}:
        raise ValueError("Неизвестный источник измерения")
    if not isinstance(record.get("meta", {}), dict):
        raise ValueError("Некорректные метаданные измерения")
    return {**record, "timestamp": stamp, "rate": validate_number(record["rate"])}


class RatesStorage:
    """Хранит историю отдельно от кеша, сохраняя старые данные при ошибке API."""

    _thread_lock = RLock()

    def __init__(self, config=None):
        self.config = config or ParserConfig()
        self.rates_path = Path(self.config.RATES_FILE_PATH)
        self.history_path = Path(self.config.HISTORY_FILE_PATH)

    @contextmanager
    def _locked(self):
        """Блокирует ручной и периодический парсеры, включая разные процессы."""
        self.rates_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.rates_path.with_suffix(".lock")
        with self._thread_lock, lock_path.open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read(path, default, expected):
        """Отсутствующий файл допустим, повреждённый не перезаписывается."""
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return default
        if not isinstance(data, expected):
            raise ValueError(f"Неверная структура {path.name}")
        return data

    def save_measurements(self, records):
        """Добавляет уникальные измерения и строит кеш из самой новой истории."""
        records = [validate_record(record) for record in records]
        if not records:
            raise ValueError("Нет измерений для сохранения")
        with self._locked():
            history = self._read(self.history_path, [], list)
            history = [validate_record(record) for record in history]
            old = self._read(self.rates_path, {}, dict)
            old_pairs = old.get("pairs", old)
            if not isinstance(old_pairs, dict):
                raise ValueError("Поле pairs должно быть объектом")
            pairs = {}
            for pair, entry in old_pairs.items():
                if not isinstance(entry, dict) or "_" not in pair:
                    continue
                if entry.get("source") == "DemoStub":
                    continue
                # Невалидный реальный кеш не заменяем молча.
                validate_number(entry["rate"])
                utc_timestamp(entry["updated_at"])
                pairs[pair] = dict(entry)
            by_id = {item["id"]: item for item in history}
            added = 0
            for record in records:
                if record["id"] not in by_id:
                    by_id[record["id"]] = record
                    added += 1
                elif by_id[record["id"]] != record:
                    raise ValueError("Конфликт измерений с одинаковым ID")
            # Вся история позволяет восстановить кеш после сбоя между двумя записями.
            changed = set()
            for record in by_id.values():
                pair = f"{record['from_currency']}_{record['to_currency']}"
                previous = pairs.get(pair)
                if previous is None or (
                    datetime.fromisoformat(record["timestamp"])
                    > datetime.fromisoformat(previous["updated_at"])
                ):
                    pairs[pair] = {
                        "rate": record["rate"],
                        "updated_at": record["timestamp"],
                        "source": record["source"],
                    }
                    changed.add(pair)
            last_refresh = old.get("last_refresh")
            if changed:
                last_refresh = utc_now().isoformat().replace("+00:00", "Z")
            snapshot = {"pairs": pairs, "last_refresh": last_refresh}
            # При ошибке первой записи кеш остаётся старым. Каждая замена атомарна.
            atomic_write_json(self.history_path, list(by_id.values()))
            atomic_write_json(self.rates_path, snapshot)
            return {
                "updated": len(changed),
                "added": added,
                "last_refresh": last_refresh,
            }
