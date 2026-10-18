"""Иерархия валют и фабрика; капитализации ниже — учебные примеры."""

import math
from abc import ABC, abstractmethod

from valutatrade_hub.core.exceptions import CurrencyNotFoundError


def normalize_code(code):
    """Нормализует ввод CLI; объект Currency хранит только строгий код."""
    if not isinstance(code, str):
        raise ValueError("Код валюты должен быть строкой")
    code = code.strip().upper()
    if not 2 <= len(code) <= 5 or not code.isascii() or not code.isalpha():
        raise ValueError("Код валюты: 2–5 латинских букв без пробелов")
    return code


def nonempty(value, field):
    """Проверяет обязательное строковое поле."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} не может быть пустым")
    return value.strip()


class Currency(ABC):
    """Общий интерфейс валюты с валидируемыми публичными свойствами."""

    def __init__(self, name, code):
        """Инициализирует и проверяет обязательные атрибуты валюты."""
        self.name = name
        self.code = code

    @property
    def name(self):
        """Человекочитаемое название."""
        return self._name

    @name.setter
    def name(self, value):
        """Проверяет непустое название перед изменением."""
        self._name = nonempty(value, "Название")

    @property
    def code(self):
        """Код в верхнем регистре."""
        return self._code

    @code.setter
    def code(self, value):
        """Проверяет строгий формат кода перед изменением."""
        normalized = normalize_code(value)
        if value != normalized:
            raise ValueError("Код объекта валюты должен быть в верхнем регистре")
        self._code = normalized

    @abstractmethod
    def get_display_info(self) -> str:
        """Возвращает описание для интерфейса и журнала."""


class FiatCurrency(Currency):
    """Фиатная валюта с указанием страны или зоны эмиссии."""

    def __init__(self, name, code, issuing_country):
        """Инициализирует и проверяет обязательные атрибуты валюты."""
        super().__init__(name, code)
        self.issuing_country = nonempty(issuing_country, "Страна эмиссии")

    def get_display_info(self):
        """Форматирует описание фиатной валюты."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """Криптовалюта с алгоритмом и последней известной капитализацией."""

    def __init__(self, name, code, algorithm, market_cap):
        """Инициализирует и проверяет обязательные атрибуты валюты."""
        super().__init__(name, code)
        self.algorithm = nonempty(algorithm, "Алгоритм")
        if (
            isinstance(market_cap, bool)
            or not isinstance(market_cap, (int, float))
            or not math.isfinite(market_cap)
            or market_cap < 0
        ):
            raise ValueError("Капитализация должна быть конечной и неотрицательной")
        self.market_cap = float(market_cap)

    def get_display_info(self):
        """Форматирует капитализацию в краткой экспоненциальной записи."""
        mantissa, exponent = f"{self.market_cap:.2e}".split("e")
        cap = f"{mantissa.rstrip('0').rstrip('.')}e{int(exponent)}"
        return (
            f"[CRYPTO] {self.code} — {self.name} (Algo: {self.algorithm}, MCAP: {cap})"
        )


CURRENCY_REGISTRY = {
    "USD": (FiatCurrency, ("US Dollar", "USD", "United States")),
    "EUR": (FiatCurrency, ("Euro", "EUR", "Eurozone")),
    "GBP": (FiatCurrency, ("Pound Sterling", "GBP", "United Kingdom")),
    "SOL": (CryptoCurrency, ("Solana", "SOL", "Proof of Stake", 0.0)),
    "RUB": (FiatCurrency, ("Russian Ruble", "RUB", "Russia")),
    "BTC": (CryptoCurrency, ("Bitcoin", "BTC", "SHA-256", 1.12e12)),
    "ETH": (CryptoCurrency, ("Ethereum", "ETH", "Proof of Stake", 4.5e11)),
}


def get_currency(code: str) -> Currency:
    """Создаёт валюту из реестра; вызывающий не может изменить общий объект."""
    code = normalize_code(code)
    if code not in CURRENCY_REGISTRY:
        raise CurrencyNotFoundError(code)
    factory, arguments = CURRENCY_REGISTRY[code]
    return factory(*arguments)
