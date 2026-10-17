"""Проверки инкапсуляции и инвариантов моделей."""

import unittest
from datetime import datetime

from valutatrade_hub.core.models import Portfolio, User, Wallet


class UserTests(unittest.TestCase):
    def test_hash_salt_and_public_information(self):
        first = User.create(1, "alice", "secret-password")
        second = User.create(2, "bob", "secret-password")
        self.assertTrue(first.verify_password("secret-password"))
        self.assertFalse(first.verify_password("wrong"))
        self.assertNotEqual(first.salt, second.salt)
        self.assertNotEqual(first.hashed_password, second.hashed_password)
        self.assertEqual(
            set(first.get_user_info()),
            {
                "user_id",
                "username",
                "registration_date",
            },
        )
        self.assertIsInstance(first.registration_date, datetime)
        self.assertTrue(
            User.from_dict(first.to_dict()).verify_password("secret-password")
        )

    def test_password_change_and_validation(self):
        user = User.create(1, "alice", "1234")
        old_hash, old_salt = user.hashed_password, user.salt
        with self.assertRaises(ValueError):
            user.change_password("abc")
        self.assertEqual(user.hashed_password, old_hash)
        user.change_password("abcd")
        self.assertFalse(user.verify_password("1234"))
        self.assertTrue(user.verify_password("abcd"))
        self.assertNotEqual(user.salt, old_salt)
        with self.assertRaises(ValueError):
            user.username = " "
        self.assertEqual(user.username, "alice")
        with self.assertRaises(AttributeError):
            user.user_id = 9

    def test_invalid_constructor_data(self):
        for user_id in [0, -1, True, 1.5]:
            with self.subTest(user_id=user_id), self.assertRaises(ValueError):
                User.create(user_id, "alice", "1234")
        with self.assertRaises(ValueError):
            User.create(1, " ", "1234")


class WalletTests(unittest.TestCase):
    def test_deposit_withdraw_and_balance_info(self):
        wallet = Wallet("usd", 10)
        wallet.deposit(2.5)
        wallet.withdraw(1.5)
        self.assertEqual(
            wallet.get_balance_info(), {"currency_code": "USD", "balance": 11}
        )
        wallet.withdraw(11)
        self.assertEqual(wallet.balance, 0)

    def test_invalid_numbers_do_not_mutate_balance(self):
        wallet = Wallet("USD", 10)
        for amount in [-1, 0, True, "1", float("nan"), float("inf"), 10**1000]:
            for operation in [wallet.deposit, wallet.withdraw]:
                with self.subTest(amount=amount), self.assertRaises(ValueError):
                    operation(amount)
                self.assertEqual(wallet.balance, 10)
        for balance in [-1, True, "1", float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                wallet.balance = balance
        with self.assertRaises(ValueError):
            wallet.withdraw(11)
        self.assertEqual(wallet.balance, 10)

    def test_overflow_and_immutable_currency(self):
        wallet = Wallet("USD", 1e308)
        with self.assertRaises(ValueError):
            wallet.deposit(1e308)
        self.assertEqual(wallet.balance, 1e308)
        with self.assertRaises(AttributeError):
            wallet.currency_code = "BTC"


class PortfolioTests(unittest.TestCase):
    def test_wallets_owner_and_conversion(self):
        user = User.create(1, "alice", "1234")
        portfolio = Portfolio(1, user=user, exchange_rates={"USD": 1, "EUR": 2})
        portfolio.add_currency("USD").deposit(100)
        portfolio.add_currency("EUR").deposit(50)
        self.assertEqual(portfolio.get_total_value(), 200)
        self.assertEqual(portfolio.get_total_value("EUR"), 100)
        self.assertIs(portfolio.user, user)
        portfolio.wallets.clear()
        self.assertEqual(len(portfolio.wallets), 2)
        with self.assertRaises(AttributeError):
            portfolio.user = user
        with self.assertRaises(ValueError):
            portfolio.add_currency("usd")
        with self.assertRaises(ValueError):
            portfolio.get_wallet("BTC")
        with self.assertRaises(ValueError):
            portfolio.get_total_value("ABC")
        restored = Portfolio.from_dict(portfolio.to_dict(), user)
        self.assertEqual(restored.get_wallet("EUR").balance, 50)

    def test_owner_must_match(self):
        with self.assertRaises(ValueError):
            Portfolio(1, user=User.create(2, "bob", "1234"))
