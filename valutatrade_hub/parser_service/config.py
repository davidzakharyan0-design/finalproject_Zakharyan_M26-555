"""Параметры источников; секреты читаются только из окружения при создании."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from valutatrade_hub.infra.settings import SettingsLoader

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
EXCHANGERATE_API_URL = "https://v6.exchangerate-api.com/v6"
FIAT_CURRENCIES = ("EUR", "GBP", "RUB")
CRYPTO_CURRENCIES = ("BTC", "ETH", "SOL")
CRYPTO_ID_MAP = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
REQUEST_TIMEOUT = 10
UPDATE_INTERVAL = 300


@dataclass
class ParserConfig:
    """Структурированные настройки; repr намеренно не содержит ключей."""

    EXCHANGERATE_API_KEY: str = field(
        default_factory=lambda: os.getenv("EXCHANGERATE_API_KEY", ""), repr=False
    )
    COINGECKO_API_KEY: str = field(
        default_factory=lambda: os.getenv("COINGECKO_API_KEY", ""), repr=False
    )
    COINGECKO_URL: str = COINGECKO_URL
    EXCHANGERATE_API_URL: str = EXCHANGERATE_API_URL
    BASE_CURRENCY: str = "USD"
    FIAT_CURRENCIES: tuple = FIAT_CURRENCIES
    CRYPTO_CURRENCIES: tuple = CRYPTO_CURRENCIES
    CRYPTO_ID_MAP: dict = field(default_factory=lambda: dict(CRYPTO_ID_MAP))
    REQUEST_TIMEOUT: float = REQUEST_TIMEOUT
    RATES_FILE_PATH: str = ""
    HISTORY_FILE_PATH: str = ""
    LOG_FILE_PATH: str = ""

    def __post_init__(self):
        """Согласует пути с основным сервисом и проверяет сетевые параметры."""
        from valutatrade_hub.core.utils import validate_number

        self.REQUEST_TIMEOUT = validate_number(self.REQUEST_TIMEOUT)
        if self.BASE_CURRENCY != "USD":
            raise ValueError("Текущий формат Parser Service использует базу USD")
        settings = SettingsLoader()
        data_dir = Path(settings.get("data_dir"))
        if not self.RATES_FILE_PATH:
            self.RATES_FILE_PATH = str(data_dir / settings.get("rates_file"))
        if not self.HISTORY_FILE_PATH:
            self.HISTORY_FILE_PATH = str(data_dir / "exchange_rates.json")
        if not self.LOG_FILE_PATH:
            self.LOG_FILE_PATH = str(
                Path(settings.get("log_file")).with_name("parser.log")
            )
        if (
            Path(self.RATES_FILE_PATH).resolve()
            == Path(self.HISTORY_FILE_PATH).resolve()
        ):
            raise ValueError("История и кеш должны быть разными файлами")
