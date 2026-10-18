"""Предсказуемые ошибки предметной области для CLI и журнала."""


class InsufficientFundsError(ValueError):
    """Операция требует больше средств, чем доступно в кошельке."""

    def __init__(self, available, required, code):
        """Сохраняет контекст ошибки и формирует пользовательское сообщение."""
        self.available, self.required, self.code = available, required, code
        super().__init__(
            f"Недостаточно средств: доступно {available} {code}, "
            f"требуется {required} {code}"
        )


class CurrencyNotFoundError(ValueError):
    """Кода нет в реестре поддерживаемых валют."""

    def __init__(self, code):
        """Сохраняет контекст ошибки и формирует пользовательское сообщение."""
        self.code = code
        super().__init__(f"Неизвестная валюта '{code}'")


class ApiRequestError(ValueError):
    """Поставщик не смог предоставить пригодный свежий курс."""

    def __init__(self, reason):
        """Сохраняет контекст ошибки и формирует пользовательское сообщение."""
        self.reason = reason
        super().__init__(f"Ошибка при обращении к внешнему API: {reason}")
