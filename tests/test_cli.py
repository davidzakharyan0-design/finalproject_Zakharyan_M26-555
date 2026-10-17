"""Проверки интерфейса и отдельного процесса приложения."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from valutatrade_hub.cli.interface import CommandInterface, parse_command
from valutatrade_hub.core.usecases import WalletService
from valutatrade_hub.core.utils import JsonStorage


class CliTests(unittest.TestCase):
    def test_parser(self):
        self.assertEqual(
            parse_command('register --username "Alice Smith" --password abcd'),
            (
                "register",
                {"username": "Alice Smith", "password": "abcd"},
            ),
        )
        for text in [
            "register --username alice",
            "help --bad value",
            "buy --currency BTC --amount x",
            "login --username alice --username bob --password abcd",
            'login --username "bad',
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_command(text)

    def test_loop_recovers_and_no_password_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            interface = CommandInterface(WalletService(JsonStorage(directory)))
            commands = [
                "register --username alice --password secret-phrase",
                "buy --currency BTC --amount 1",
                "login --username alice --password secret-phrase",
                "deposit --amount nan",
                "deposit --amount 100",
                "buy --currency EUR --amount 1",
                "show-portfolio",
                "exit",
            ]
            with patch("builtins.input", side_effect=commands):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    interface.run()
            text = output.getvalue()
            self.assertIn("Сначала выполните login", text)
            self.assertIn("Покупка выполнена", text)
            self.assertIn("DemoStub", text)
            self.assertNotIn("secret-phrase", text)

    def test_separate_process_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            environment = {**os.environ, "PYTHONPATH": root}
            command = [sys.executable, os.path.join(root, "main.py")]
            first = subprocess.run(
                command,
                cwd=directory,
                env=environment,
                input=(
                    "register --username alice --password 1234\n"
                    "login --username alice --password 1234\n"
                    "deposit --amount 100\nbuy --currency EUR --amount 1\nexit\n"
                ),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Покупка выполнена", first.stdout)
            second = subprocess.run(
                command,
                cwd=directory,
                env=environment,
                input=(
                    "login --username alice --password 1234\nshow-portfolio\n"
                    "sell --currency EUR --amount 1\nshow-portfolio\nexit\n"
                ),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Продажа выполнена", second.stdout)
            self.assertIn("100.00 USD", second.stdout)
            self.assertFalse(second.stderr)
