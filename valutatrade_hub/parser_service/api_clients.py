"""HTTP-клиенты: один запрос на источник, проверка ответа и безопасные ошибки."""

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import utc_now, validate_number
from valutatrade_hub.parser_service.config import ParserConfig


def timestamp(value):
    """Проверяет UNIX-время поставщика и переводит его в ISO UTC."""
    number = validate_number(value)
    result = datetime.fromtimestamp(number, timezone.utc)
    if result > utc_now():
        raise ValueError("Время поставщика находится в будущем")
    return result.isoformat()


class BaseApiClient(ABC):
    """Единый контракт пары→курс; метаданные последнего ответа доступны отдельно."""

    source = "API"
    source_key = "api"

    def __init__(self, config=None, session=None):
        self.config = config or ParserConfig()
        self.session = session or requests
        self.observed_at = None
        self.metadata = {}

    @abstractmethod
    def fetch_rates(self) -> dict:
        """Возвращает полностью проверенные положительные курсы к USD."""

    def _get(self, url, *, params=None, headers=None):
        """Не включает URL, ключ, тело ответа или текст сетевого исключения в ошибку."""
        self.metadata = {}
        self.observed_at = None
        started = time.monotonic()
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.config.REQUEST_TIMEOUT,
                allow_redirects=False,
            )
        except requests.exceptions.RequestException:
            raise ApiRequestError(f"{self.source}: ошибка сети или таймаут") from None
        if response.status_code != 200:
            reasons = {
                401: "требуется корректный API-ключ",
                403: "доступ запрещён",
                429: "лимит запросов превышен; повторите позже",
            }
            reason = reasons.get(response.status_code, "неуспешный ответ")
            raise ApiRequestError(
                f"{self.source}: HTTP {response.status_code}, {reason}"
            )
        try:
            data = response.json()
        except ValueError:
            raise ApiRequestError(f"{self.source}: ответ не является JSON") from None
        if not isinstance(data, dict):
            raise ApiRequestError(f"{self.source}: ожидался JSON-объект")
        self.observed_at = utc_now().isoformat()
        self.request_meta = {
            "request_ms": round((time.monotonic() - started) * 1000, 3),
            "status_code": response.status_code,
            "etag": response.headers.get("ETag"),
        }
        return data


class CoinGeckoClient(BaseApiClient):
    """Получает криптовалюты одним запросом simple/price."""

    source = "CoinGecko"
    source_key = "coingecko"

    def fetch_rates(self):
        """Преобразует ID монет в тикеры; неполный ответ не сохраняется."""
        config = self.config
        try:
            ids = [config.CRYPTO_ID_MAP[code] for code in config.CRYPTO_CURRENCIES]
        except KeyError:
            raise ApiRequestError(
                "CoinGecko: отсутствует ID монеты в конфигурации"
            ) from None
        headers = {}
        if config.COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY
        data = self._get(
            config.COINGECKO_URL,
            params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            },
            headers=headers,
        )
        rates, metadata = {}, {}
        try:
            for code, raw_id in zip(config.CRYPTO_CURRENCIES, ids, strict=True):
                get_currency(code)
                item = data[raw_id]
                pair = f"{code}_USD"
                rates[pair] = validate_number(item["usd"])
                meta = {**self.request_meta, "raw_id": raw_id}
                if "last_updated_at" in item:
                    meta["provider_updated_at"] = timestamp(item["last_updated_at"])
                metadata[pair] = meta
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ApiRequestError(
                "CoinGecko: неполные или некорректные котировки"
            ) from None
        self.metadata = metadata
        return rates


class ExchangeRateApiClient(BaseApiClient):
    """Получает фиатные курсы; переводит USD→валюта в валюта→USD."""

    source = "ExchangeRate-API"
    source_key = "exchangerate"

    def fetch_rates(self):
        """Читает conversion_rates (реальный API) или rates (пример задания)."""
        config = self.config
        if not config.EXCHANGERATE_API_KEY:
            raise ApiRequestError("ExchangeRate-API: задайте EXCHANGERATE_API_KEY")
        key = quote(config.EXCHANGERATE_API_KEY, safe="")
        url = f"{config.EXCHANGERATE_API_URL}/{key}/latest/{config.BASE_CURRENCY}"
        data = self._get(url)
        if data.get("result") != "success":
            reasons = {
                "invalid-key": "неверный API-ключ",
                "inactive-account": "аккаунт не активирован",
                "quota-reached": "лимит запросов исчерпан",
                "unsupported-code": "база не поддерживается",
            }
            kind = data.get("error-type")
            reason = (
                reasons.get(kind, "API вернул ошибку")
                if isinstance(kind, str)
                else "API вернул ошибку"
            )
            raise ApiRequestError(f"ExchangeRate-API: {reason}")
        if data.get("base_code") != config.BASE_CURRENCY:
            raise ApiRequestError("ExchangeRate-API: неверная база ответа")
        values = data.get("conversion_rates", data.get("rates"))
        rates, metadata = {}, {}
        try:
            provider_time = data.get("time_last_update_unix")
            for code in config.FIAT_CURRENCIES:
                get_currency(code)
                pair = f"{code}_USD"
                rates[pair] = validate_number(1 / validate_number(values[code]))
                metadata[pair] = {**self.request_meta, "raw_id": code}
                if provider_time is not None:
                    metadata[pair]["provider_updated_at"] = timestamp(provider_time)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ApiRequestError(
                "ExchangeRate-API: неполные или неверные курсы"
            ) from None
        self.metadata = metadata
        return rates
