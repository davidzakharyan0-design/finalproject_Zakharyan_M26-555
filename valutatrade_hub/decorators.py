"""Журналирование торговых операций с пробросом исходных исключений."""

import inspect
import math
from functools import wraps

from valutatrade_hub.logging_config import configure_logging


def safe_amount(value):
    """Не допускает NaN/Infinity в JSON и не записывает произвольный ввод."""
    if type(value) in (int, float):
        try:
            if math.isfinite(value):
                return value
        except OverflowError:
            pass
    return None


def log_action(action=None, *, verbose=False):
    """Добавляет INFO-запись результата; никогда не сохраняет пароль или соль."""

    def decorate(function):
        """Оборачивает выбранную функцию журналированием."""
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(self, *args, **kwargs):
            """Записывает исход операции и сохраняет исходное исключение."""
            logger = configure_logging()
            bound = signature.bind(self, *args, **kwargs).arguments
            user = self.current_user
            currency = bound.get("currency")
            record = {
                "action": action or function.__name__.upper(),
                "user_id": user.user_id if user else None,
                "username": user.username if user else None,
                "currency_code": currency.upper()
                if isinstance(currency, str)
                else None,
                "amount": safe_amount(bound.get("amount")),
                "rate": None,
                "base": "USD",
            }
            try:
                result = function(self, *args, **kwargs)
            except Exception as error:
                record.update(
                    result="ERROR",
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                logger.info("action", extra={"action_data": record})
                raise
            record.update(result="OK", rate=result.get("quote", {}).get("rate"))
            if verbose:
                record["context"] = {
                    key: result[key]
                    for key in ("before", "after", "usd_before", "usd_after")
                    if key in result
                }
            logger.info("action", extra={"action_data": record})
            return result

        return wrapped

    return decorate
