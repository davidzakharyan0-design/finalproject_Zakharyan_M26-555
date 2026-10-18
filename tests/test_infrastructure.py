"""Проверки валют, конфигурации, единого хранилища и журнала операций."""

import io
import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from tests.helpers import seed_rates
from valutatrade_hub.cli.interface import CommandInterface
from valutatrade_hub.core.constants import PORTFOLIOS_FILE, RATES_FILE, USERS_FILE
from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    Currency,
    FiatCurrency,
    get_currency,
)
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
)
from valutatrade_hub.core.models import Wallet
from valutatrade_hub.core.usecases import WalletService
from valutatrade_hub.core.utils import RateService, utc_now
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import configure_logging


class CurrencyTests(unittest.TestCase):
    def test_abstract_contract_and_display(self):
        with self.assertRaises(TypeError):
            Currency("US Dollar", "USD")
        self.assertEqual(
            get_currency("usd").get_display_info(),
            "[FIAT] USD — US Dollar (Issuing: United States)",
        )
        self.assertEqual(
            get_currency("BTC").get_display_info(),
            "[CRYPTO] BTC — Bitcoin (Algo: SHA-256, MCAP: 1.12e12)",
        )
        self.assertIsInstance(get_currency("EUR"), FiatCurrency)
        self.assertIsInstance(get_currency("ETH"), CryptoCurrency)
        currency = get_currency("USD")
        currency.name = "Changed"
        self.assertEqual(get_currency("USD").name, "US Dollar")

    def test_invalid_currency_and_mutations(self):
        for code in ("U", "TOOLONG", "US D", "usd", " USD", "РУБ", "US1"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                FiatCurrency("Dollar", code, "US")
        with self.assertRaises(ValueError):
            FiatCurrency(" ", "USD", "US")
        with self.assertRaises(ValueError):
            CryptoCurrency("Bitcoin", "BTC", "SHA-256", float("nan"))
        currency = get_currency("USD")
        with self.assertRaises(ValueError):
            currency.code = "usd"
        self.assertEqual(currency.code, "USD")
        with self.assertRaisesRegex(CurrencyNotFoundError, "Неизвестная валюта 'XYZ'"):
            get_currency("XYZ")

    def test_wallet_error_has_required_fields(self):
        wallet = Wallet("USD", 10)
        with self.assertRaises(InsufficientFundsError) as caught:
            wallet.withdraw(11)
        self.assertEqual(caught.exception.available, 10)
        self.assertEqual(caught.exception.required, 11)
        self.assertEqual(caught.exception.code, "USD")
        self.assertEqual(wallet.balance, 10)


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / "pyproject.toml"
        self.config.write_text(
            '[tool.valutatrade]\ndata_dir = "state"\n'
            'users_file = "accounts.json"\nrates_ttl_seconds = 2\n'
            'default_base_currency = "EUR"\n'
            'log_file = "audit/actions.log"\nlog_max_bytes = 700\n'
            "log_backup_count = 2\n",
            encoding="utf-8",
        )
        # Изолируем глобальные экземпляры только внутри теста и восстанавливаем их.
        settings_patch = patch.object(SettingsLoader, "_instance", None)
        database_patch = patch.object(DatabaseManager, "_instance", None)
        settings_patch.start()
        database_patch.start()
        self.addCleanup(settings_patch.stop)
        self.addCleanup(database_patch.stop)
        self.settings = SettingsLoader(self.config)
        self.database = DatabaseManager()
        self.addCleanup(self.close_log)

    @staticmethod
    def close_log():
        logger = logging.getLogger("valutatrade.actions")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def service(self):
        seed_rates(self.database)
        service = WalletService()
        service.register("alice", "secret-password")
        service.login("alice", "secret-password")
        return service

    def test_singleton_paths_and_repeated_construction(self):
        self.assertIs(SettingsLoader(), self.settings)
        self.assertIs(DatabaseManager(), self.database)
        self.assertIs(WalletService().storage, self.database)
        self.database.save(USERS_FILE, [{"example": 1}])
        self.assertEqual(DatabaseManager().load(USERS_FILE), [{"example": 1}])
        self.assertTrue((self.directory / "state/accounts.json").exists())
        self.assertFalse((self.directory / "state/users.json").exists())
        with ThreadPoolExecutor(max_workers=4) as pool:
            instances = list(pool.map(lambda _: DatabaseManager(), range(20)))
        self.assertTrue(all(item is self.database for item in instances))
        with self.assertRaises(ValueError):
            SettingsLoader(self.directory / "another.toml")

    def test_reload_validates_before_replacing(self):
        self.config.write_text("[tool.valutatrade]\nrates_ttl_seconds = 9\n")
        self.settings.reload()
        self.assertEqual(self.settings.get("rates_ttl_seconds"), 9)
        self.assertEqual(self.settings.get("missing", "fallback"), "fallback")
        self.config.write_text("[tool.valutatrade]\nrates_ttl_seconds = -1\n")
        with self.assertRaises(ValueError):
            self.settings.reload()
        self.assertEqual(self.settings.get("rates_ttl_seconds"), 9)
        self.config.write_text('[tool.valutatrade]\nusers_file = "../users.json"\n')
        with self.assertRaises(ValueError):
            self.settings.reload()

    def test_configuration_ttl_and_api_failure_preserve_cache(self):
        expired = {
            "USD_BTC": {
                "rate": 0.1,
                "updated_at": (utc_now() - timedelta(seconds=3)).isoformat(),
            }
        }
        self.database.save(RATES_FILE, expired)
        provider = Mock(side_effect=OSError("offline"))
        rates = RateService(self.database, provider=provider)
        self.assertEqual(rates.ttl, 2)
        with self.assertRaises(ApiRequestError):
            rates.get_rate("USD", "BTC")
        self.assertEqual(self.database.load(RATES_FILE), expired)
        with self.assertRaises(CurrencyNotFoundError):
            rates.get_rate("XYZ", "XYZ")
        provider.assert_called_once()

    def test_default_base_and_funds_errors(self):
        service = self.service()
        self.assertEqual(service.show_portfolio()["base"], "EUR")
        for operation in (service.buy, service.sell):
            with self.assertRaises(InsufficientFundsError):
                operation("BTC", 1)
        self.assertEqual(self.database.load(PORTFOLIOS_FILE)[0]["wallets"], {})

    def test_logging_success_error_context_and_no_secret(self):
        service = self.service()
        service.deposit(100)
        logger = configure_logging()
        self.assertIs(configure_logging(), logger)
        self.assertEqual(len(logger.handlers), 1)
        with self.assertLogs(logger, level="INFO") as captured:
            service.buy("EUR", 1)
            with self.assertRaises(InsufficientFundsError):
                service.sell("EUR", 2)
        records = [record.action_data for record in captured.records]
        self.assertEqual([record["result"] for record in records], ["OK", "ERROR"])
        self.assertEqual(records[0]["username"], "alice")
        self.assertEqual(records[0]["rate"], 1.0786)
        self.assertEqual(records[0]["context"]["after"], 1)
        self.assertEqual(records[1]["error_type"], "InsufficientFundsError")
        self.assertNotIn("secret-password", str(records))
        self.assertNotIn("hashed_password", str(records))
        self.assertEqual(service.buy.__name__, "buy")

    def test_rotation_and_valid_json_for_invalid_amount(self):
        service = self.service()
        service.deposit(100)
        for _ in range(8):
            with self.assertRaises(ValueError):
                service.buy("EUR", float("nan"))
        files = sorted((self.directory / "audit").glob("actions.log*"))
        self.assertEqual(len(files), 3)
        for path in files:
            for line in path.read_text().splitlines():
                record = json.loads(line)
                self.assertIn("timestamp", record)
                self.assertEqual(record["level"], "INFO")
                self.assertEqual(record["result"], "ERROR")
                self.assertIsNone(record["amount"])

    def test_transaction_prevents_lost_updates(self):
        service = self.service()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: service.deposit(1), range(30)))
        wallet = self.database.load(PORTFOLIOS_FILE)[0]["wallets"]["USD"]
        self.assertEqual(wallet["balance"], 30)

    def test_cli_domain_errors_recover(self):
        service = self.service()
        self.database.save(RATES_FILE, {})
        service.rates.provider = Mock(side_effect=OSError("offline"))
        output = io.StringIO()
        commands = [
            "sell --currency BTC --amount 1",
            "get-rate --from XYZ --to USD",
            "get-rate --from BTC --to USD",
            "exit",
        ]
        with patch("builtins.input", side_effect=commands), redirect_stdout(output):
            CommandInterface(service).run()
        text = output.getvalue()
        self.assertIn("Недостаточно средств", text)
        self.assertIn("Неизвестная валюта 'XYZ'", text)
        self.assertIn("Повторите позже", text)
        self.assertIn("До свидания!", text)
