"""Сценарии регистрации, входа и управления виртуальным портфелем."""

from valutatrade_hub.core.constants import PORTFOLIOS_FILE, USERS_FILE
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.utils import (
    JsonStorage,
    RateService,
    validate_currency,
    validate_number,
)


class WalletService:
    """Бизнес-логика приложения с сессией текущего пользователя."""

    def __init__(self, storage=None, rates=None):
        """Принимает заменяемые зависимости для хранилища и курсов."""
        self.storage = storage if storage is not None else JsonStorage()
        self.rates = rates if rates is not None else RateService(self.storage)
        self._current_user = None

    @property
    def current_user(self):
        """Возвращает текущего пользователя или None."""
        return self._current_user

    def register(self, username, password):
        """Создаёт пользователя и пустой портфель, не выполняя вход."""
        users = self.storage.load(USERS_FILE)
        portfolios = self.storage.load(PORTFOLIOS_FILE)
        next_id = max((row["user_id"] for row in users), default=0) + 1
        user = User.create(next_id, username, password)
        if any(row["username"] == user.username for row in users):
            raise ValueError(f"Имя пользователя '{user.username}' уже занято")
        portfolio = Portfolio(user.user_id, user=user)
        # Сохраняем старый документ для отката при ошибке второго файла.
        self.storage.save(PORTFOLIOS_FILE, portfolios + [portfolio.to_dict()])
        try:
            self.storage.save(USERS_FILE, users + [user.to_dict()])
        except OSError:
            self.storage.save(PORTFOLIOS_FILE, portfolios)
            raise
        return user.get_user_info()

    def login(self, username, password):
        """Проверяет пароль и открывает сессию; неудачный вход сбрасывает её."""
        self._current_user = None
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        username = username.strip()
        for record in self.storage.load(USERS_FILE):
            if record["username"] == username:
                user = User.from_dict(record)
                if not user.verify_password(password):
                    raise ValueError("Неверный пароль")
                self._current_user = user
                return user.get_user_info()
        raise ValueError(f"Пользователь '{username}' не найден")

    def logout(self):
        """Завершает текущую сессию."""
        self._current_user = None

    def _require_login(self):
        """Запрещает доступ к портфелю без аутентификации."""
        if self.current_user is None:
            raise ValueError("Сначала выполните login")
        return self.current_user

    def _load_portfolio(self):
        """Читает только портфель текущего пользователя."""
        user = self._require_login()
        for record in self.storage.load(PORTFOLIOS_FILE):
            if record["user_id"] == user.user_id:
                return Portfolio.from_dict(record, user)
        raise ValueError("Портфель пользователя не найден в хранилище")

    def _save_portfolio(self, portfolio):
        """Сохраняет все кошельки пользователя одной заменой JSON-файла."""
        records = self.storage.load(PORTFOLIOS_FILE)
        for index, record in enumerate(records):
            if record["user_id"] == portfolio.user_id:
                records[index] = portfolio.to_dict()
                self.storage.save(PORTFOLIOS_FILE, records)
                return
        raise ValueError("Портфель пользователя не найден в хранилище")

    def deposit(self, amount):
        """Пополняет виртуальный USD-кошелёк, создавая его при необходимости."""
        portfolio = self._load_portfolio()
        amount = validate_number(amount)
        wallet = (
            portfolio.get_wallet("USD")
            if "USD" in portfolio.wallets
            else portfolio.add_currency("USD")
        )
        before = wallet.balance
        wallet.deposit(amount)
        self._save_portfolio(portfolio)
        return {"before": before, "after": wallet.balance, "amount": amount}

    def get_rate(self, from_currency, to_currency):
        """Делегирует получение котировки единому сервису курсов."""
        return self.rates.get_rate(from_currency, to_currency)

    def show_portfolio(self, base="USD"):
        """Возвращает строки отчёта и общую стоимость по свежим котировкам."""
        portfolio = self._load_portfolio()
        base = validate_currency(base)
        try:
            self.rates.get_rate(base, base)
        except ValueError as error:
            raise ValueError(f"Неизвестная базовая валюта '{base}'") from error
        rows = []
        sources = set()
        total = 0.0
        for code, wallet in sorted(portfolio.wallets.items()):
            quote = self.rates.get_rate(code, base)
            value = validate_number(wallet.balance * quote["rate"], positive=False)
            total += value
            sources.add(quote["source"])
            rows.append({"currency": code, "balance": wallet.balance, "value": value})
        return {
            "username": portfolio.user.username,
            "base": base,
            "rows": rows,
            "total": validate_number(total, positive=False),
            "sources": sorted(sources),
        }

    def buy(self, currency, amount):
        """Покупает валюту за USD; списание и зачисление сохраняются вместе."""
        portfolio = self._load_portfolio()
        currency = validate_currency(currency)
        amount = validate_number(amount)
        if currency == "USD":
            raise ValueError("Для пополнения USD используйте deposit --amount <сумма>")
        quote = self.rates.get_rate(currency, "USD")
        cost = validate_number(amount * quote["rate"])
        if "USD" not in portfolio.wallets:
            raise ValueError("Нет средств USD. Пополните: deposit --amount <сумма>")
        usd = portfolio.get_wallet("USD")
        wallet = (
            portfolio.get_wallet(currency)
            if currency in portfolio.wallets
            else portfolio.add_currency(currency)
        )
        before = wallet.balance
        after = validate_number(before + amount)
        usd_before = usd.balance
        usd.withdraw(cost)
        wallet.balance = after
        self._save_portfolio(portfolio)
        return {
            "currency": currency,
            "amount": amount,
            "before": before,
            "after": after,
            "usd_before": usd_before,
            "usd_after": usd.balance,
            "value": cost,
            "quote": quote,
        }

    def sell(self, currency, amount):
        """Продаёт валюту и начисляет выручку в USD одной записью портфеля."""
        portfolio = self._load_portfolio()
        currency = validate_currency(currency)
        amount = validate_number(amount)
        if currency == "USD":
            raise ValueError("Продажа USD за USD не является обменом валют")
        wallet = portfolio.get_wallet(currency)
        before = wallet.balance
        wallet.withdraw(amount)
        quote = self.rates.get_rate(currency, "USD")
        value = validate_number(amount * quote["rate"])
        usd = (
            portfolio.get_wallet("USD")
            if "USD" in portfolio.wallets
            else portfolio.add_currency("USD")
        )
        usd_before = usd.balance
        usd.deposit(value)
        self._save_portfolio(portfolio)
        return {
            "currency": currency,
            "amount": amount,
            "before": before,
            "after": wallet.balance,
            "usd_before": usd_before,
            "usd_after": usd.balance,
            "value": value,
            "quote": quote,
        }
