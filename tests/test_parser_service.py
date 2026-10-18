"""Контракты API, сохранение истории, отказоустойчивость и кеш без сети."""

import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from valutatrade_hub.cli.interface import CommandInterface, parse_command
from valutatrade_hub.core.constants import RATES_FILE
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.usecases import WalletService
from valutatrade_hub.core.utils import JsonStorage, RateService, utc_now
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.scheduler import run_scheduler
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import RatesUpdater


def response(data, status=200):
    result = Mock(status_code=status, headers={"ETag": "test-etag"})
    result.json.return_value = data
    return result


def measurement(pair="BTC_USD", rate=100, stamp=None, source="CoinGecko"):
    stamp = (stamp or utc_now().isoformat()).replace("+00:00", "Z")
    left, right = pair.split("_")
    return {
        "id": f"{pair}_{stamp}",
        "from_currency": left,
        "to_currency": right,
        "rate": rate,
        "timestamp": stamp,
        "source": source,
        "meta": {},
    }


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = ParserConfig(
            EXCHANGERATE_API_KEY="test-secret-never-print",
            COINGECKO_API_KEY="cg-secret-never-print",
            RATES_FILE_PATH=str(self.directory / "rates.json"),
            HISTORY_FILE_PATH=str(self.directory / "exchange_rates.json"),
            LOG_FILE_PATH=str(self.directory / "parser.log"),
        )
        self.storage = RatesStorage(self.config)
        self.database = JsonStorage(self.directory)
        self.addCleanup(self.close_logger)

    def close_logger(self):
        logger = logging.getLogger(
            f"valutatrade.parser.{self.directory / 'parser.log'}"
        )
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def crypto(self, data=None):
        if data is None:
            data = {
                "bitcoin": {"usd": 100},
                "ethereum": {"usd": 10},
                "solana": {"usd": 2},
            }
        session = Mock()
        session.get.return_value = response(data)
        return CoinGeckoClient(self.config, session)

    def fiat(self, data=None):
        if data is None:
            data = {
                "result": "success",
                "base_code": "USD",
                "conversion_rates": {"EUR": 0.5, "GBP": 0.25, "RUB": 100},
                "time_last_update_unix": int(utc_now().timestamp()) - 10000,
            }
        session = Mock()
        session.get.return_value = response(data)
        return ExchangeRateApiClient(self.config, session)

    def test_config_reads_environment_at_creation_and_hides_secrets(self):
        with patch.dict("os.environ", {"EXCHANGERATE_API_KEY": "late-key"}):
            self.assertEqual(ParserConfig().EXCHANGERATE_API_KEY, "late-key")
        self.assertNotIn("test-secret-never-print", repr(self.config))
        with self.assertRaises(TypeError):
            BaseApiClient()
        with self.assertRaises(ValueError):
            ParserConfig(REQUEST_TIMEOUT=0)
        with self.assertRaises(ValueError):
            ParserConfig(BASE_CURRENCY="EUR")

    def test_crypto_batch_request_and_metadata(self):
        client = self.crypto()
        self.assertEqual(
            client.fetch_rates(), {"BTC_USD": 100, "ETH_USD": 10, "SOL_USD": 2}
        )
        arguments = client.session.get.call_args.kwargs
        self.assertEqual(arguments["params"]["ids"], "bitcoin,ethereum,solana")
        self.assertEqual(arguments["params"]["vs_currencies"], "usd")
        self.assertEqual(
            arguments["headers"]["x-cg-demo-api-key"], "cg-secret-never-print"
        )
        self.assertEqual(arguments["timeout"], 10)
        self.assertFalse(arguments["allow_redirects"])
        self.assertEqual(client.metadata["BTC_USD"]["raw_id"], "bitcoin")
        self.assertEqual(client.metadata["BTC_USD"]["etag"], "test-etag")

    def test_fiat_inverts_api_direction_and_keeps_provider_time(self):
        client = self.fiat()
        self.assertEqual(
            client.fetch_rates(), {"EUR_USD": 2, "GBP_USD": 4, "RUB_USD": 0.01}
        )
        self.assertIn("provider_updated_at", client.metadata["EUR_USD"])
        self.assertIn("/latest/USD", client.session.get.call_args.args[0])

    def test_assignment_rates_alias(self):
        client = self.fiat(
            {
                "result": "success",
                "base_code": "USD",
                "rates": {"EUR": 0.5, "GBP": 0.25, "RUB": 100},
            }
        )
        self.assertEqual(client.fetch_rates()["EUR_USD"], 2)

    def test_missing_key_does_not_request(self):
        self.config.EXCHANGERATE_API_KEY = ""
        client = self.fiat()
        with self.assertRaisesRegex(ApiRequestError, "EXCHANGERATE_API_KEY"):
            client.fetch_rates()
        client.session.get.assert_not_called()

    def test_network_http_json_errors_do_not_leak_key(self):
        client = self.fiat()
        client.session.get.side_effect = requests.exceptions.Timeout(
            "https://host/test-secret-never-print"
        )
        with self.assertRaises(ApiRequestError) as caught:
            client.fetch_rates()
        self.assertNotIn("test-secret", str(caught.exception))
        client.session.get.side_effect = None
        for status in (401, 403, 429, 500, 302):
            client.session.get.return_value = response({}, status)
            with self.subTest(status=status), self.assertRaises(ApiRequestError):
                client.fetch_rates()
        bad = response({})
        bad.json.side_effect = ValueError("test-secret-never-print")
        client.session.get.return_value = bad
        with self.assertRaises(ApiRequestError) as caught:
            client.fetch_rates()
        self.assertNotIn("test-secret", str(caught.exception))

    def test_invalid_responses_rejected_entirely(self):
        for value in (0, -1, True, "1", None, float("nan"), float("inf")):
            client = self.crypto({"bitcoin": {"usd": value}})
            with self.subTest(value=value), self.assertRaises(ApiRequestError):
                client.fetch_rates()
            self.assertEqual(client.metadata, {})
        for data in (
            {"result": "error", "error-type": "invalid-key"},
            {"result": "success", "base_code": "EUR", "rates": {}},
            {"result": "success", "base_code": "USD", "rates": {"EUR": 0}},
        ):
            with self.assertRaises(ApiRequestError):
                self.fiat(data).fetch_rates()

    def test_updater_full_success_and_history_contract(self):
        updater = RatesUpdater([self.crypto(), self.fiat()], self.storage, self.config)
        result = updater.run_update()
        self.assertEqual(result["updated"], 6)
        self.assertEqual(result["errors"], [])
        snapshot = self.database.load(RATES_FILE)
        self.assertEqual(set(snapshot), {"pairs", "last_refresh"})
        history = json.loads(Path(self.config.HISTORY_FILE_PATH).read_text())
        self.assertEqual(len(history), 6)
        self.assertTrue(all(item["id"].endswith(item["timestamp"]) for item in history))
        self.assertEqual(snapshot["pairs"]["EUR_USD"]["rate"], 2)
        self.assertEqual(RateService(self.database).get_rate("BTC", "EUR")["rate"], 50)
        self.assertNotIn(
            "test-secret-never-print", Path(self.config.LOG_FILE_PATH).read_text()
        )

    def test_partial_success_preserves_other_pairs(self):
        previous = measurement("EUR_USD", 2, source="ExchangeRate-API")
        self.storage.save_measurements([previous])
        self.config.EXCHANGERATE_API_KEY = ""
        updater = RatesUpdater([self.crypto(), self.fiat()], self.storage, self.config)
        result = updater.run_update()
        self.assertEqual(result["updated"], 3)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self.database.load(RATES_FILE)["pairs"]["EUR_USD"]["rate"], 2)

    def test_total_failure_preserves_bytes(self):
        self.storage.save_measurements([measurement()])
        before = Path(self.config.RATES_FILE_PATH).read_bytes()
        client = self.crypto()
        client.session.get.side_effect = requests.exceptions.ConnectionError()
        updater = RatesUpdater([client], self.storage, self.config)
        with self.assertRaises(ApiRequestError):
            updater.run_update()
        self.assertEqual(Path(self.config.RATES_FILE_PATH).read_bytes(), before)

    def test_source_filter_and_invalid_source(self):
        crypto, fiat = self.crypto(), self.fiat()
        updater = RatesUpdater([crypto, fiat], self.storage, self.config)
        self.assertEqual(updater.run_update("coingecko")["updated"], 3)
        fiat.session.get.assert_not_called()
        with self.assertRaises(ValueError):
            updater.run_update("unknown")

    def test_deduplication_and_out_of_order_updates(self):
        newest = measurement(rate=200)
        self.storage.save_measurements([newest])
        result = self.storage.save_measurements([newest])
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["updated"], 0)
        old = measurement(rate=100, stamp=(utc_now() - timedelta(hours=1)).isoformat())
        self.storage.save_measurements([old])
        self.assertEqual(
            self.database.load(RATES_FILE)["pairs"]["BTC_USD"]["rate"], 200
        )
        self.assertEqual(
            len(json.loads(Path(self.config.HISTORY_FILE_PATH).read_text())), 2
        )
        with self.assertRaises(ValueError):
            self.storage.save_measurements([{**newest, "rate": 999}])

    def test_invalid_measurement_and_corrupt_history_preserve_cache(self):
        self.storage.save_measurements([measurement()])
        before = Path(self.config.RATES_FILE_PATH).read_bytes()
        with self.assertRaises(ValueError):
            self.storage.save_measurements([measurement(rate=float("nan"))])
        Path(self.config.HISTORY_FILE_PATH).write_text("{broken")
        with self.assertRaises(ValueError):
            self.storage.save_measurements([measurement()])
        self.assertEqual(Path(self.config.RATES_FILE_PATH).read_bytes(), before)
        self.assertEqual(Path(self.config.HISTORY_FILE_PATH).read_text(), "{broken")

    def test_atomic_write_failure_and_recovery_from_history(self):
        from valutatrade_hub.core.utils import atomic_write_json

        self.storage.save_measurements([measurement(rate=100)])
        before = Path(self.config.RATES_FILE_PATH).read_bytes()
        new = measurement(rate=200)

        def fail_snapshot(path, data):
            if Path(path) == Path(self.config.RATES_FILE_PATH):
                raise OSError("disk full")
            atomic_write_json(path, data)

        with (
            patch(
                "valutatrade_hub.parser_service.storage.atomic_write_json",
                side_effect=fail_snapshot,
            ),
            self.assertRaises(OSError),
        ):
            self.storage.save_measurements([new])
        self.assertEqual(Path(self.config.RATES_FILE_PATH).read_bytes(), before)
        self.storage.save_measurements([new])
        self.assertEqual(
            self.database.load(RATES_FILE)["pairs"]["BTC_USD"]["rate"], 200
        )

    def test_demo_cache_not_used_or_mixed(self):
        self.database.save(
            RATES_FILE,
            {
                "BTC_USD": {
                    "rate": 10,
                    "updated_at": utc_now().isoformat(),
                    "source": "DemoStub",
                }
            },
        )
        with self.assertRaisesRegex(ApiRequestError, "update-rates"):
            RateService(self.database).get_rate("BTC", "USD")
        self.storage.save_measurements(
            [measurement("EUR_USD", 2, source="ExchangeRate-API")]
        )
        self.assertNotIn("BTC_USD", self.database.load(RATES_FILE)["pairs"])

    def test_core_never_requests_network_on_expired_cache(self):
        self.storage.save_measurements(
            [measurement(stamp=(utc_now() - timedelta(hours=1)).isoformat())]
        )
        before = Path(self.config.RATES_FILE_PATH).read_bytes()
        with patch("requests.get") as get:
            with self.assertRaisesRegex(ApiRequestError, "update-rates"):
                RateService(self.database).get_rate("BTC", "USD")
            get.assert_not_called()
        self.assertEqual(Path(self.config.RATES_FILE_PATH).read_bytes(), before)

    def test_show_rates_filters_top_cross_and_empty(self):
        rates = RateService(self.database)
        with self.assertRaisesRegex(ValueError, "кеш курсов пуст"):
            rates.list_rates()
        RatesUpdater(
            [self.crypto(), self.fiat()], self.storage, self.config
        ).run_update()
        self.assertEqual(
            [r["pair"] for r in rates.list_rates(top=2)["rows"]], ["BTC_USD", "ETH_USD"]
        )
        self.assertEqual(rates.list_rates(currency="RUB")["rows"][0]["rate"], 0.01)
        self.assertEqual(
            rates.list_rates(currency="BTC", base="EUR")["rows"][0]["rate"], 50
        )
        for top in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                rates.list_rates(top=top)
        with self.assertRaises(ValueError):
            parse_command("show-rates --top abc")

    def test_scheduler_continues_after_failure_without_real_sleep(self):
        updater = Mock()
        updater.run_update.side_effect = [
            ApiRequestError("offline"),
            {"updated": 3, "errors": []},
        ]
        sleep = Mock()
        with redirect_stdout(io.StringIO()):
            run_scheduler(updater, interval=60, cycles=2, sleep=sleep)
        self.assertEqual(updater.run_update.call_count, 2)
        sleep.assert_called_once_with(60)
        with self.assertRaises(ValueError):
            run_scheduler(updater, interval=0, cycles=1, sleep=sleep)

    def test_cli_update_and_show_rates(self):
        updater = RatesUpdater([self.crypto()], self.storage, self.config)
        interface = CommandInterface(WalletService(self.database))
        with (
            patch(
                "valutatrade_hub.parser_service.updater.RatesUpdater",
                return_value=updater,
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            interface.execute("update-rates --source coingecko")
            interface.execute("show-rates --top 2")
        self.assertIn("Обновлено пар: 3", output.getvalue())
        self.assertIn("BTC_USD", output.getvalue())

    def test_updater_rejects_entire_invalid_client_batch(self):
        client = self.crypto()
        client.fetch_rates = Mock(return_value={"BTC_USD": 100, "ETH_USD": -1})
        updater = RatesUpdater([client, self.fiat()], self.storage, self.config)
        result = updater.run_update()
        self.assertEqual(result["updated"], 3)
        self.assertEqual(len(result["errors"]), 1)
        self.assertNotIn("BTC_USD", self.database.load(RATES_FILE)["pairs"])

    def test_atomic_replace_error_preserves_old_document(self):
        from valutatrade_hub.core.utils import atomic_write_json

        path = self.directory / "atomic.json"
        atomic_write_json(path, {"old": True})
        before = path.read_bytes()
        with (
            patch("valutatrade_hub.core.utils.os.replace", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            atomic_write_json(path, {"new": True})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
