"""Координатор источников: частичный успех, журнал шагов, сохранение измерений."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import utc_now
from valutatrade_hub.logging_config import ActionFormatter
from valutatrade_hub.parser_service.api_clients import (
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage, utc_timestamp

_LOG_LOCK = RLock()


def parser_logger(config):
    """Настраивает отдельный ротируемый JSON-журнал без HTTP URL и ключей."""
    path = Path(config.LOG_FILE_PATH).resolve()
    logger = logging.getLogger(f"valutatrade.parser.{path}")
    with _LOG_LOCK:
        if not logger.handlers:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                path, maxBytes=1048576, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(ActionFormatter())
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
    return logger


class RatesUpdater:
    """Принимает клиентов и хранилище; сбой источника не отменяет другие ответы."""

    def __init__(self, clients=None, storage=None, config=None):
        self.config = config or ParserConfig()
        self.clients = (
            clients
            if clients is not None
            else [CoinGeckoClient(self.config), ExchangeRateApiClient(self.config)]
        )
        self.storage = storage or RatesStorage(self.config)
        self.logger = parser_logger(self.config)

    def _log(self, event, *, error=False, **fields):
        self.logger.log(
            logging.ERROR if error else logging.INFO,
            event,
            extra={"action_data": {"action": event, **fields}},
        )

    def run_update(self, source=None):
        """Обновляет все источники или один; возвращает сводку для CLI/планировщика."""
        if source not in (None, "coingecko", "exchangerate"):
            raise ValueError("source: coingecko или exchangerate")
        clients = [c for c in self.clients if source is None or c.source_key == source]
        if not clients:
            raise ValueError("Нет клиентов выбранного источника")
        self._log("UPDATE_START", source=source or "all")
        records, errors = [], []
        for client in clients:
            self._log("FETCH_START", source=client.source)
            try:
                rates = client.fetch_rates()
                if not rates:
                    raise ApiRequestError(f"{client.source}: пустой ответ")
                stamp = utc_timestamp(client.observed_at or utc_now().isoformat())
                batch = []
                for pair, rate in rates.items():
                    from_code, to_code = pair.split("_")
                    batch.append(
                        {
                            "id": f"{pair}_{stamp}",
                            "from_currency": from_code,
                            "to_currency": to_code,
                            "rate": rate,
                            "timestamp": stamp,
                            "source": client.source,
                            "meta": client.metadata.get(pair, {}),
                        }
                    )
                from valutatrade_hub.parser_service.storage import validate_record

                validated = [validate_record(record) for record in batch]
                records.extend(validated)
                self._log("FETCH_OK", source=client.source, count=len(batch))
            except (ApiRequestError, ValueError, KeyError, TypeError) as error:
                # Только ожидаемые ошибки клиента, без URL, response.text или traceback.
                message = (
                    str(error)
                    if isinstance(error, ApiRequestError)
                    else (f"{client.source}: некорректный ответ")
                )
                errors.append(message)
                self._log(
                    "FETCH_ERROR", error=True, source=client.source, reason=message
                )
        if not records:
            self._log("UPDATE_ERROR", error=True, reason="Нет успешных источников")
            raise ApiRequestError("; ".join(errors))
        try:
            result = self.storage.save_measurements(records)
        except (ValueError, OSError, KeyError, TypeError):
            self._log("WRITE_ERROR", error=True, reason="Не удалось сохранить данные")
            raise
        self._log("UPDATE_END", **result, errors=len(errors))
        return {**result, "errors": errors}
