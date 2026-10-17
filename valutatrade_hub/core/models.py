"""Пользователь, валютный кошелёк и портфель пользователя."""

import hashlib
import hmac
import secrets
from datetime import datetime

from valutatrade_hub.core.constants import DEFAULT_USD_RATES, MIN_PASSWORD_LENGTH
from valutatrade_hub.core.utils import (
    utc_now,
    validate_currency,
    validate_number,
    validate_user_id,
)


class User:
    """Хранит учётные данные; открытый пароль не сохраняется."""

    def __init__(self, user_id, username, hashed_password, salt, registration_date):
        """Восстанавливает пользователя из всех полей сохранённой записи."""
        self._user_id = validate_user_id(user_id)
        self.username = username
        if not isinstance(salt, str) or not salt:
            raise ValueError("Соль не может быть пустой")
        if (
            not isinstance(hashed_password, str)
            or len(hashed_password) != 64
            or any(character not in "0123456789abcdef" for character in hashed_password)
        ):
            raise ValueError("Некорректный SHA-256 хеш пароля")
        if not isinstance(registration_date, datetime):
            raise ValueError("Дата регистрации должна иметь тип datetime")
        self._hashed_password = hashed_password
        self._salt = salt
        self._registration_date = registration_date

    @staticmethod
    def _validate_password(password):
        """Проверяет минимальную длину и строковый тип пароля."""
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError("Пароль должен быть не короче 4 символов")

    @staticmethod
    def _hash(password, salt):
        """Реализует учебную схему SHA-256(password + salt) из задания."""
        return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()

    @classmethod
    def create(cls, user_id, username, password):
        """Создаёт нового пользователя с уникальной случайной солью."""
        cls._validate_password(password)
        salt = secrets.token_hex(16)
        return cls(user_id, username, cls._hash(password, salt), salt, utc_now())

    @property
    def user_id(self):
        """Возвращает неизменяемый ID."""
        return self._user_id

    @property
    def username(self):
        """Возвращает имя пользователя."""
        return self._username

    @username.setter
    def username(self, value):
        """Запрещает пустое имя и удаляет пробелы по краям."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        self._username = value.strip()

    @property
    def hashed_password(self):
        """Возвращает хеш только для сохранения учётных данных."""
        return self._hashed_password

    @property
    def salt(self):
        """Возвращает соль, необходимую для восстановления пользователя."""
        return self._salt

    @property
    def registration_date(self):
        """Возвращает неизменяемую дату регистрации."""
        return self._registration_date

    def get_user_info(self):
        """Возвращает публичные сведения без пароля, хеша и соли."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "registration_date": self.registration_date.isoformat(),
        }

    def change_password(self, new_password):
        """Меняет пароль и соль после успешной валидации."""
        self._validate_password(new_password)
        salt = secrets.token_hex(16)
        self._hashed_password = self._hash(new_password, salt)
        self._salt = salt

    def verify_password(self, password):
        """Сравнивает хеш введённого пароля с сохранённым хешем."""
        if not isinstance(password, str):
            return False
        return hmac.compare_digest(
            self._hash(password, self.salt), self.hashed_password
        )

    def to_dict(self):
        """Возвращает запись JSON, включая хеш и соль, но не открытый пароль."""
        return {
            **self.get_user_info(),
            "hashed_password": self.hashed_password,
            "salt": self.salt,
        }

    @classmethod
    def from_dict(cls, record):
        """Восстанавливает пользователя из JSON-записи."""
        return cls(
            record["user_id"],
            record["username"],
            record["hashed_password"],
            record["salt"],
            datetime.fromisoformat(record["registration_date"]),
        )


class Wallet:
    """Кошелёк одной валюты с неотрицательным конечным балансом."""

    def __init__(self, currency_code, balance=0.0):
        """Создаёт кошелёк и проверяет начальный баланс."""
        self._currency_code = validate_currency(currency_code)
        self.balance = balance

    @property
    def currency_code(self):
        """Возвращает неизменяемый код валюты."""
        return self._currency_code

    @property
    def balance(self):
        """Возвращает текущий баланс."""
        return self._balance

    @balance.setter
    def balance(self, value):
        """Запрещает отрицательные, нечисловые и бесконечные балансы."""
        self._balance = validate_number(value, positive=False)

    def deposit(self, amount):
        """Пополняет кошелёк положительной суммой."""
        self.balance = self.balance + validate_number(amount)

    def withdraw(self, amount):
        """Списывает сумму только при достаточном балансе."""
        amount = validate_number(amount)
        if amount > self.balance:
            raise ValueError(
                f"Недостаточно средств: доступно {self.balance:.4f} "
                f"{self.currency_code}, требуется {amount:.4f} {self.currency_code}"
            )
        self.balance = self.balance - amount

    def get_balance_info(self):
        """Возвращает сведения о валюте и балансе."""
        return {"currency_code": self.currency_code, "balance": self.balance}


class Portfolio:
    """Объединяет кошельки одного пользователя и вычисляет их стоимость."""

    def __init__(self, user_id, wallets=None, *, user, exchange_rates=None):
        """Принимает ID, словарь кошельков, владельца и учебные курсы к USD."""
        self._user_id = validate_user_id(user_id)
        if not isinstance(user, User) or user.user_id != user_id:
            raise ValueError("Пользователь не соответствует владельцу портфеля")
        self._user = user
        self._wallets = dict(wallets) if wallets is not None else {}
        for code, wallet in self._wallets.items():
            if not isinstance(wallet, Wallet) or code != wallet.currency_code:
                raise ValueError("Некорректный словарь кошельков")
        self._exchange_rates = dict(
            DEFAULT_USD_RATES if exchange_rates is None else exchange_rates
        )

    @property
    def user_id(self):
        """Возвращает ID владельца."""
        return self._user_id

    @property
    def user(self):
        """Возвращает владельца без возможности присвоить другого."""
        return self._user

    @property
    def wallets(self):
        """Возвращает копию словаря; сами кошельки остаются доступными объектами."""
        return dict(self._wallets)

    def add_currency(self, currency_code):
        """Создаёт новый кошелёк, запрещая дубликаты валют."""
        code = validate_currency(currency_code)
        if code in self._wallets:
            raise ValueError(f"Кошелёк '{code}' уже существует")
        wallet = Wallet(code)
        self._wallets[code] = wallet
        return wallet

    def get_wallet(self, currency_code):
        """Возвращает кошелёк или сообщает о его отсутствии."""
        code = validate_currency(currency_code)
        if code not in self._wallets:
            raise ValueError(
                f"У вас нет кошелька '{code}'. "
                "Он создаётся автоматически при первой покупке."
            )
        return self._wallets[code]

    def get_total_value(self, base_currency="USD"):
        """Считает стоимость по фиксированным курсам к USD, переданным модели."""
        base = validate_currency(base_currency)
        if base not in self._exchange_rates:
            raise ValueError(f"Неизвестная базовая валюта '{base}'")
        base_rate = validate_number(self._exchange_rates[base])
        total = 0.0
        for code, wallet in self._wallets.items():
            if code not in self._exchange_rates:
                raise ValueError(f"Не удалось получить курс для {code}→{base}")
            rate = validate_number(self._exchange_rates[code]) / base_rate
            total += wallet.balance * rate
        return validate_number(total, positive=False)

    def to_dict(self):
        """Возвращает запись портфеля в формате задания."""
        return {
            "user_id": self.user_id,
            "wallets": {
                code: {"balance": wallet.balance}
                for code, wallet in self._wallets.items()
            },
        }

    @classmethod
    def from_dict(cls, record, user):
        """Восстанавливает объекты кошельков из сохранённого портфеля."""
        wallets = {
            code: Wallet(code, values["balance"])
            for code, values in record["wallets"].items()
        }
        return cls(record["user_id"], wallets, user=user)
