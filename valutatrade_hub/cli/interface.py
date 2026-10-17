"""Единственная точка обработки пользовательских команд."""

import shlex

from prettytable import PrettyTable

from valutatrade_hub.core.usecases import WalletService

COMMAND_OPTIONS = {
    "register": ({"username", "password"}, set()),
    "login": ({"username", "password"}, set()),
    "show-portfolio": (set(), {"base"}),
    "deposit": ({"amount"}, set()),
    "buy": ({"currency", "amount"}, set()),
    "sell": ({"currency", "amount"}, set()),
    "get-rate": ({"from", "to"}, set()),
    "help": (set(), set()),
    "logout": (set(), set()),
    "exit": (set(), set()),
}


def parse_command(text):
    """Разбирает команду и именованные флаги без вывода паролей в ошибках."""
    tokens = shlex.split(text)
    if not tokens:
        return None
    command = tokens[0]
    if command not in COMMAND_OPTIONS:
        raise ValueError("Неизвестная команда. Введите help")
    required, optional = COMMAND_OPTIONS[command]
    if len(tokens[1:]) % 2:
        raise ValueError("Аргументы задаются парами: --имя значение")
    options = {}
    for index in range(1, len(tokens), 2):
        option = tokens[index]
        if not option.startswith("--") or option[2:] not in required | optional:
            raise ValueError("Неизвестный аргумент команды. Введите help")
        key = option[2:]
        if key in options:
            raise ValueError("Аргумент указан повторно")
        options[key] = tokens[index + 1]
    if missing := required - options.keys():
        raise ValueError("Отсутствуют аргументы: " + ", ".join(sorted(missing)))
    if "amount" in options:
        try:
            options["amount"] = float(options["amount"])
        except ValueError as error:
            raise ValueError("'amount' должен быть положительным числом") from error
    return command, options


def print_help():
    """Печатает команды текущего этапа."""
    print("\nValutaTrade Hub — виртуальный валютный кошелёк")
    print("Курсы DemoStub учебные и не являются актуальными рыночными котировками.")
    print("register --username <имя> --password <пароль>")
    print("login --username <имя> --password <пароль>")
    print("deposit --amount <сумма USD> — пополнение виртуального баланса")
    print("show-portfolio [--base USD]")
    print("buy --currency <код> --amount <количество валюты>")
    print("sell --currency <код> --amount <количество валюты>")
    print("get-rate --from <код> --to <код>")
    print("help | logout | exit\n")


class CommandInterface:
    """Маршрутизирует ввод в сценарии и форматирует их результаты."""

    def __init__(self, service=None):
        """Принимает бизнес-сервис для обычного запуска или тестов."""
        self.service = service if service is not None else WalletService()

    def execute(self, text):
        """Исполняет одну строку; False означает выход из цикла."""
        parsed = parse_command(text)
        if parsed is None:
            return True
        command, options = parsed
        if command == "exit":
            return False
        if command == "help":
            print_help()
        elif command == "register":
            user = self.service.register(**options)
            print(
                f"Пользователь '{user['username']}' зарегистрирован "
                f"(id={user['user_id']}). Войдите через login."
            )
        elif command == "login":
            user = self.service.login(**options)
            print(f"Вы вошли как '{user['username']}'")
        elif command == "logout":
            self.service.logout()
            print("Вы вышли из учётной записи")
        elif command == "deposit":
            result = self.service.deposit(**options)
            print(
                f"Виртуальный USD-кошелёк пополнен: {result['amount']:.2f} USD. "
                f"Баланс: {result['after']:.2f} USD"
            )
        elif command == "show-portfolio":
            result = self.service.show_portfolio(**options)
            print(f"Портфель '{result['username']}' (база: {result['base']}):")
            if not result["rows"]:
                print("Портфель пуст. Пополните USD: deposit --amount <сумма>")
            else:
                table = PrettyTable(
                    ["Валюта", "Баланс", f"Стоимость в {result['base']}"]
                )
                for row in result["rows"]:
                    table.add_row(
                        [
                            row["currency"],
                            f"{row['balance']:.8f}",
                            f"{row['value']:,.2f}",
                        ]
                    )
                print(table)
                print(f"ИТОГО: {result['total']:,.2f} {result['base']}")
                print("Источники курсов: " + ", ".join(result["sources"]))
        elif command in {"buy", "sell"}:
            result = getattr(self.service, command)(**options)
            operation = "Покупка" if command == "buy" else "Продажа"
            code = result["currency"]
            print(
                f"{operation} выполнена: {result['amount']:.4f} {code} "
                f"по курсу {result['quote']['rate']:.2f} USD/{code}"
            )
            print(
                f"- {code}: было {result['before']:.4f} → стало {result['after']:.4f}"
            )
            print(
                f"- USD: было {result['usd_before']:.2f} "
                f"→ стало {result['usd_after']:.2f}"
            )
            print(f"Стоимость сделки: {result['value']:,.2f} USD")
            print(f"Источник курса: {result['quote']['source']}")
        elif command == "get-rate":
            quote = self.service.get_rate(options["from"], options["to"])
            print(
                f"Курс {quote['from']}→{quote['to']}: {quote['rate']:.8f} "
                f"(обновлено: {quote['updated_at']})"
            )
            print(f"Обратный курс: {1 / quote['rate']:.8f}")
            print(f"Источник: {quote['source']}")
        return True

    def run(self):
        """Повторяет запрос после ошибки и корректно обрабатывает Ctrl-D/Ctrl-C."""
        print_help()
        while True:
            try:
                if not self.execute(input("> ")):
                    break
            except (ValueError, OSError) as error:
                print(f"Ошибка: {error}")
            except (KeyError, TypeError) as error:
                print(f"Ошибка структуры сохранённых данных ({type(error).__name__})")
            except (EOFError, KeyboardInterrupt):
                break
        print("До свидания!")


def main():
    """Создаёт интерфейс и запускает приложение."""
    try:
        CommandInterface().run()
    except (ValueError, OSError) as error:
        print(f"Не удалось открыть хранилище: {error}")
        raise SystemExit(1) from error
