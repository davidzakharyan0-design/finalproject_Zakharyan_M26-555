"""Проверки сценариев, сохранения и кеша котировок."""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from valutatrade_hub.core.constants import PORTFOLIOS_FILE, RATES_FILE, USERS_FILE
from valutatrade_hub.core.usecases import WalletService
from valutatrade_hub.core.utils import JsonStorage, RateService, utc_now


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = JsonStorage(self.directory.name)
        self.service = WalletService(self.storage)

    def register_login(self):
        self.service.register("alice", "1234")
        self.service.login("alice", "1234")

    def test_registration_creates_empty_portfolio_and_hash(self):
        self.service.register("alice", "secret-phrase")
        record = self.storage.load(USERS_FILE)[0]
        self.assertIn("salt", record)
        self.assertNotIn("password", record)
        self.assertNotIn("secret-phrase", str(record))
        self.assertEqual(
            self.storage.load(PORTFOLIOS_FILE), [{"user_id": 1, "wallets": {}}]
        )
        self.assertIsNone(self.service.current_user)
        with self.assertRaisesRegex(ValueError, "уже занято"):
            self.service.register("alice", "1234")
        with self.assertRaisesRegex(ValueError, "4 символов"):
            self.service.register("bob", "1")
        self.assertEqual(len(self.storage.load(USERS_FILE)), 1)

    def test_authentication_and_failed_login_resets_session(self):
        with self.assertRaisesRegex(ValueError, "Сначала выполните login"):
            self.service.buy("BTC", 1)
        with self.assertRaises(ValueError):
            self.service.show_portfolio()
        self.register_login()
        with self.assertRaisesRegex(ValueError, "Неверный пароль"):
            self.service.login("alice", "wrong")
        self.assertIsNone(self.service.current_user)
        with self.assertRaisesRegex(ValueError, "не найден"):
            self.service.login("bob", "1234")

    def test_funded_buy_sell_and_restart(self):
        self.register_login()
        self.assertEqual(self.service.show_portfolio()["rows"], [])
        self.service.deposit(5000)
        trade = self.service.buy("BTC", 0.05)
        self.assertAlmostEqual(trade["usd_after"], 5000 - 59337.21 * 0.05)
        self.assertAlmostEqual(self.service.show_portfolio()["total"], 5000)
        sale = self.service.sell("BTC", 0.01)
        self.assertAlmostEqual(sale["after"], 0.04)
        self.assertAlmostEqual(self.service.show_portfolio()["total"], 5000)
        restarted = WalletService(JsonStorage(self.directory.name))
        self.assertIsNone(restarted.current_user)
        restarted.login("alice", "1234")
        self.assertAlmostEqual(restarted.show_portfolio()["total"], 5000)
        self.assertAlmostEqual(restarted.show_portfolio("EUR")["total"], 5000 / 1.0786)

    def test_failed_trades_preserve_portfolio(self):
        self.register_login()
        path = Path(self.directory.name) / PORTFOLIOS_FILE
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.service.buy("BTC", 0.05)
        self.assertEqual(path.read_bytes(), before)
        self.service.deposit(100)
        before = path.read_bytes()
        for currency, amount in [
            ("BTC", 1),
            ("ABC", 1),
            ("EUR", -1),
            ("EUR", float("nan")),
        ]:
            with self.assertRaises(ValueError):
                self.service.buy(currency, amount)
            self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.service.sell("BTC", 1)
        self.service.buy("EUR", 1)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.service.sell("EUR", 2)
        self.assertEqual(path.read_bytes(), before)

    def test_unavailable_rate_does_not_persist_selling(self):
        self.register_login()
        self.service.deposit(100)
        self.service.buy("EUR", 1)
        path = Path(self.directory.name) / PORTFOLIOS_FILE
        before = path.read_bytes()
        self.service.rates = Mock()
        self.service.rates.get_rate.side_effect = ValueError("Нет курса")
        with self.assertRaises(ValueError):
            self.service.sell("EUR", 1)
        self.assertEqual(path.read_bytes(), before)

    def test_users_are_isolated(self):
        self.register_login()
        self.service.deposit(100)
        self.service.register("bob", "5678")
        self.service.login("bob", "5678")
        self.assertEqual(self.service.show_portfolio()["rows"], [])
        self.service.deposit(200)
        self.service.login("alice", "1234")
        self.assertEqual(self.service.show_portfolio()["total"], 100)
        with self.assertRaisesRegex(ValueError, "Неизвестная валюта"):
            self.service.show_portfolio("ABC")

    def test_registration_rollback_on_second_write_error(self):
        original_save = self.storage.save

        def fail_user_save(filename, data):
            if filename == USERS_FILE:
                raise OSError("disk error")
            original_save(filename, data)

        self.storage.save = fail_user_save
        with self.assertRaises(OSError):
            self.service.register("alice", "1234")
        self.assertEqual(self.storage.load(PORTFOLIOS_FILE), [])
        self.assertEqual(self.storage.load(USERS_FILE), [])

    def test_broken_json_is_not_overwritten(self):
        path = Path(self.directory.name) / USERS_FILE
        path.write_text("{bad", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.register("alice", "1234")
        self.assertEqual(path.read_text(), "{bad")


class RateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = JsonStorage(self.directory.name)

    def test_inverse_cross_and_no_provider_call_for_fresh_cache(self):
        now = utc_now().isoformat()
        self.storage.save(
            RATES_FILE,
            {
                "EUR_USD": {"rate": 2, "updated_at": now},
                "BTC_USD": {"rate": 100, "updated_at": now},
                "source": "TestProvider",
            },
        )
        provider = Mock(side_effect=OSError("offline"))
        rates = RateService(self.storage, provider)
        self.assertEqual(rates.get_rate("USD", "BTC")["rate"], 0.01)
        self.assertEqual(rates.get_rate("EUR", "BTC")["rate"], 0.02)
        self.assertEqual(rates.get_rate("BTC", "USD")["source"], "TestProvider")
        provider.assert_not_called()

    def test_expired_cache_refreshes_once(self):
        self.storage.save(
            RATES_FILE,
            {
                "EUR_USD": {
                    "rate": 2,
                    "updated_at": (utc_now() - timedelta(minutes=10)).isoformat(),
                }
            },
        )
        provider = Mock(
            return_value={
                "rate": 3,
                "updated_at": utc_now().isoformat(),
                "source": "Test",
            }
        )
        rates = RateService(self.storage, provider)
        self.assertEqual(rates.get_rate("EUR", "USD")["rate"], 3)
        self.assertEqual(rates.get_rate("EUR", "USD")["rate"], 3)
        provider.assert_called_once()

    def test_unavailable_and_invalid_provider(self):
        for provider in [
            Mock(side_effect=OSError("offline")),
            Mock(
                return_value={"rate": float("nan"), "updated_at": utc_now().isoformat()}
            ),
        ]:
            with self.assertRaises(ValueError):
                RateService(self.storage, provider).get_rate("USD", "BTC")
        with self.assertRaises(ValueError):
            RateService(self.storage).get_rate("ABC", "ABC")
        self.assertEqual(RateService(self.storage).get_rate("USD", "USD")["rate"], 1)
