"""Ручной запуск парсера или ограниченный по частоте цикл; никаких фоновых установок."""

import argparse
import time

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.parser_service.config import (
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    UPDATE_INTERVAL,
)
from valutatrade_hub.parser_service.updater import RatesUpdater


def run_scheduler(
    updater, interval=UPDATE_INTERVAL, source=None, *, cycles=None, sleep=time.sleep
):
    """Обновляет последовательно; ждёт интервал после ответа, включая ошибку API."""
    if (
        type(interval) not in (int, float)
        or not MIN_UPDATE_INTERVAL <= interval <= MAX_UPDATE_INTERVAL
    ):
        raise ValueError("Интервал должен быть от 60 до 86400 секунд")
    count = 0
    while cycles is None or count < cycles:
        try:
            result = updater.run_update(source)
            print(
                f"Обновлено пар: {result['updated']}; ошибок: {len(result['errors'])}"
            )
        except (ApiRequestError, ValueError, OSError) as error:
            print(f"Обновление не выполнено: {error}")
        count += 1
        if cycles is None or count < cycles:
            sleep(interval)


def main():
    """Запускается как valutatrade-parser или python -m ...scheduler."""
    parser = argparse.ArgumentParser(description="Обновление локального кеша валют")
    parser.add_argument("--source", choices=("coingecko", "exchangerate"))
    parser.add_argument("--interval", type=int, default=UPDATE_INTERVAL)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        updater = RatesUpdater()
        if args.once:
            result = updater.run_update(args.source)
            print(f"Обновлено пар: {result['updated']}")
            for error in result["errors"]:
                print(error)
            if result["errors"]:
                raise SystemExit(1)
        else:
            run_scheduler(updater, args.interval, args.source)
    except (ApiRequestError, ValueError, OSError) as error:
        parser.exit(1, f"Ошибка: {error}\n")
    except KeyboardInterrupt:
        print("\nПланировщик остановлен")


if __name__ == "__main__":
    main()
