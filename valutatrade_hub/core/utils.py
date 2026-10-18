"""Валидация, JSON-хранилище и единый контракт получения курса."""

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from valutatrade_hub.core.constants import (
    DATA_DIR,
    DEFAULT_USD_RATES,
    DEMO_SOURCE,
    PORTFOLIOS_FILE,
    RATES_FILE,
    USERS_FILE,
)
from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.infra.settings import SettingsLoader


def utc_now():
    """Возвращает текущее время с часовым поясом UTC."""
    return datetime.now(timezone.utc)


def validate_currency(value):
    """Проверяет код валюты и возвращает его в верхнем регистре."""
    return get_currency(value).code


def validate_number(value, *, positive=True):
    """Проверяет конечное число; bool, NaN и бесконечность запрещены."""
    message = "'amount' должен быть положительным числом"
    if not positive:
        message = "Баланс должен быть неотрицательным конечным числом"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(message)
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(message) from error
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(message)
    return number


def validate_user_id(value):
    """Проверяет положительный целочисленный идентификатор."""
    if type(value) is not int or value <= 0:
        raise ValueError("user_id должен быть положительным целым числом")
    return value


def demo_rate(from_currency, to_currency):
    """Предоставляет учебный курс; не обращается к биржам или API."""
    if from_currency not in DEFAULT_USD_RATES or to_currency not in DEFAULT_USD_RATES:
        raise ValueError(f"Курс {from_currency}→{to_currency} недоступен")
    return {
        "rate": DEFAULT_USD_RATES[from_currency] / DEFAULT_USD_RATES[to_currency],
        "updated_at": utc_now().isoformat(),
        "source": DEMO_SOURCE,
    }


class JsonStorage:
    """Хранит документы в заданной папке с атомарной заменой каждого файла."""

    def __init__(self, data_dir=DATA_DIR, filenames=None):
        """Создаёт отсутствующие файлы, не перезаписывая существующие данные."""
        self._filenames = filenames or {
            name: name for name in (USERS_FILE, PORTFOLIOS_FILE, RATES_FILE)
        }
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for filename, default in (
            (USERS_FILE, []),
            (PORTFOLIOS_FILE, []),
            (RATES_FILE, {}),
        ):
            if not (self.data_dir / self._filenames[filename]).exists():
                self.save(filename, default)

    def load(self, filename):
        """Читает JSON и проверяет ожидаемый тип корневого объекта."""
        if filename not in {USERS_FILE, PORTFOLIOS_FILE, RATES_FILE}:
            raise ValueError("Неизвестный файл хранилища")
        with (self.data_dir / self._filenames[filename]).open(encoding="utf-8") as file:
            data = json.load(file)
        expected = dict if filename == RATES_FILE else list
        if not isinstance(data, expected):
            raise ValueError(f"Некорректная структура файла {filename}")
        return data

    def save(self, filename, data):
        """Пишет временный файл и заменяет целевой только после успешной записи."""
        if filename not in {USERS_FILE, PORTFOLIOS_FILE, RATES_FILE}:
            raise ValueError("Неизвестный файл хранилища")
        atomic_write_json(self.data_dir / self._filenames[filename], data)


def atomic_write_json(path, data):
    """Сериализует до записи и атомарно заменяет один JSON-документ."""
    path = Path(path)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RateService:
    """Возвращает прямые, обратные и кросс-курсы с проверкой свежести."""

    def __init__(self, storage, provider=None, ttl=None):
        """Читает кеш; provider предназначен для изолированных тестов."""
        self.storage = storage
        self.provider = provider
        self.ttl = SettingsLoader().get("rates_ttl_seconds") if ttl is None else ttl
        if type(self.ttl) is not int or self.ttl <= 0:
            raise ValueError("TTL должен быть положительным целым")

    def _fresh_quote(self, entry, default_source):
        """Возвращает пригодную запись кеша или None."""
        if not isinstance(entry, dict):
            return None
        if self.provider is None and entry.get("source") == "DemoStub":
            return None
        try:
            rate = validate_number(entry["rate"])
            updated = datetime.fromisoformat(entry["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (utc_now() - updated).total_seconds()
            if not 0 <= age < self.ttl:
                return None
            return {
                "rate": rate,
                "updated_at": updated.astimezone(timezone.utc).isoformat(),
                "source": entry.get("source", default_source),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _cached(self, cache, source, target):
        """Проверяет прямую пару и обратную пару в свежем кеше."""
        fallback_source = cache.get("source", "Cache")
        quote = self._fresh_quote(cache.get(f"{source}_{target}"), fallback_source)
        if quote is not None:
            return quote
        inverse = self._fresh_quote(cache.get(f"{target}_{source}"), fallback_source)
        if inverse is not None:
            return {**inverse, "rate": validate_number(1 / inverse["rate"])}
        return None

    def get_rate(self, from_currency, to_currency):
        """Возвращает словарь from/to/rate/updated_at/source."""
        source = validate_currency(from_currency)
        target = validate_currency(to_currency)
        cache = self.snapshot()["pairs"]
        if source == target:
            quote = {
                "rate": 1.0,
                "updated_at": utc_now().isoformat(),
                "source": "Identity",
            }
        else:
            quote = self._cached(cache, source, target)
            if quote is None and source != "USD" and target != "USD":
                left = self._cached(cache, source, "USD")
                right = self._cached(cache, target, "USD")
                if left is not None and right is not None:
                    quote = {
                        "rate": validate_number(left["rate"] / right["rate"]),
                        "updated_at": min(left["updated_at"], right["updated_at"]),
                        "source": f"{left['source']}/{right['source']}",
                    }
            if quote is None:
                if self.provider is None:
                    raise ApiRequestError(
                        f"Курс {source}→{target} отсутствует или устарел. "
                        "Выполните 'update-rates'."
                    )
                try:
                    entry = self.provider(source, target)
                    quote = self._fresh_quote(entry, "Provider")
                except ApiRequestError:
                    raise
                except Exception as error:
                    raise ApiRequestError(
                        f"Курс {source}→{target} недоступен ({type(error).__name__})"
                    ) from error
                if quote is None:
                    raise ApiRequestError(
                        f"Курс {source}→{target}: некорректный или устаревший ответ"
                    )
                cache[f"{source}_{target}"] = quote
                self.storage.save(
                    RATES_FILE, {"pairs": cache, "last_refresh": utc_now().isoformat()}
                )
        return {"from": source, "to": target, **quote}

    def snapshot(self):
        """Читает новый формат и старые плоские пары; не меняет файлы."""
        try:
            document = self.storage.load(RATES_FILE)
        except FileNotFoundError:
            document = {}
        pairs = document.get("pairs", document)
        if not isinstance(pairs, dict):
            raise ValueError("Неверная структура кеша: pairs")
        return {
            "pairs": {
                key: {"source": document.get("source", "Cache"), **value}
                for key, value in pairs.items()
                if "_" in key and isinstance(value, dict)
            },
            "last_refresh": document.get("last_refresh"),
        }

    def list_rates(self, currency=None, top=None, base="USD"):
        """Возвращает только свежие курсы, фильтр валюты и top криптовалют по цене."""
        from valutatrade_hub.core.currencies import (
            CryptoCurrency,
            get_currency,
            normalize_code,
        )

        base = get_currency(base).code
        if currency is not None:
            currency = normalize_code(currency)
        if top is not None and (type(top) is not int or top <= 0):
            raise ValueError("top должен быть положительным целым числом")
        snapshot = self.snapshot()
        pairs = snapshot["pairs"]
        if not pairs:
            raise ValueError(
                "Локальный кеш курсов пуст. Выполните 'update-rates', "
                "чтобы загрузить данные."
            )
        codes = set()
        for pair in pairs:
            codes.update(pair.split("_"))
        if currency is not None and currency not in codes:
            raise ValueError(f"Курс для '{currency}' не найден в кеше")
        rows, skipped = [], 0
        for code in sorted(codes):
            if currency is not None and code != currency:
                continue
            if code == base and currency is None:
                continue
            if top is not None and not isinstance(get_currency(code), CryptoCurrency):
                continue
            # Не зовём provider: show-rates всегда работает только с локальным кешем.
            quote = self._cached(pairs, code, base)
            if quote is None and code == base:
                quote = {
                    "rate": 1.0,
                    "updated_at": utc_now().isoformat(),
                    "source": "Identity",
                }
            if quote is None and code != "USD" and base != "USD":
                left, right = (
                    self._cached(pairs, code, "USD"),
                    self._cached(pairs, base, "USD"),
                )
                if left and right:
                    quote = {
                        "rate": validate_number(left["rate"] / right["rate"]),
                        "updated_at": min(left["updated_at"], right["updated_at"]),
                        "source": f"{left['source']}/{right['source']}",
                    }
            if quote is None:
                skipped += 1
                continue
            rows.append({"pair": f"{code}_{base}", **quote})
        if not rows:
            raise ValueError("Нет подходящих свежих курсов. Выполните 'update-rates'.")
        if top is not None:
            rows = sorted(rows, key=lambda row: (-row["rate"], row["pair"]))[:top]
        return {
            "rows": rows,
            "skipped": skipped,
            "last_refresh": snapshot["last_refresh"],
        }
