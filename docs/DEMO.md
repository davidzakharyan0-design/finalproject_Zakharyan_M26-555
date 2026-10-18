# Финальная демонстрация ValutaTrade Hub

Опубликованная запись: https://asciinema.org/a/TwletEgaE3HV6DHm
Ниже сохранён сценарий для повторной записи по пункту 7.2 критериев.

## До записи

В терминале проекта выполните `make lint` и `make test`.
Задайте EXCHANGERATE_API_KEY **до начала записи**, если открыли новый терминал:

```zsh
read -s "EXCHANGERATE_API_KEY?ExchangeRate API key: "
export EXCHANGERATE_API_KEY
printf '\n'
```

Если CoinGecko требует Demo key, аналогично задайте COINGECKO_API_KEY.
Не показывайте переменные окружения, ключи или HTTP URL с ключом на записи.
Пароль demo1234 ниже предназначен только для нового демонстрационного аккаунта.

## Запись

```bash
export ASCIINEMA_CONFIG_HOME="$HOME/.asciinema-config"
mkdir -p "$ASCIINEMA_CONFIG_HOME"
uvx asciinema rec valutatrade-final.cast
```

Если файл уже существует, выберите новое имя, например `valutatrade-final-2.cast`.
Команды после начала записи:

```bash
make run
```

Внутри приложения вводите по одной:

```text
update-rates
show-rates --top 2
show-rates --currency RUB
register --username final_demo --password demo1234
login --username final_demo --password demo1234
deposit --amount 5000
buy --currency BTC --amount 0.001
show-portfolio
sell --currency BTC --amount 0.0005
show-portfolio
get-rate --from EUR --to USD
sell --currency BTC --amount 999999
get-rate --from XYZ --to USD
exit
```

Если final_demo уже существует, используйте новое имя для обеих команд.
Ожидаются успешная покупка/продажа и две понятные ошибки; ошибки не должны
завершать приложение. При истёкшем TTL выполните update-rates ещё раз.
Если обновление частичное, восстановите доступ к источнику и повторите запись,
чтобы показать крипто- и фиатные курсы.

После выхода из приложения можно показать журнал:

```bash
tail -n 2 logs/actions.log
```

Затем **в терминале записи** выполните второй `exit`, чтобы закончить asciinema:

```bash
exit
```

Только после сообщения о завершении записи:

```bash
uvx asciinema play valutatrade-final.cast
uvx asciinema upload valutatrade-final.cast
```

Убедитесь, что запись связана с вашим аккаунтом asciinema. Если upload выдаёт
предупреждение о временном хранении, выполните `uvx asciinema auth` и откройте
выданную персональную ссылку самостоятельно. Не публикуйте ссылку авторизации.

## Встраивание в README

После upload возьмите публичную ссылку вида `https://asciinema.org/a/ID`.
Добавьте в README раздел «Демонстрация» и строку, заменив ID в обоих местах:

```markdown
[![ValutaTrade Hub — полный сценарий](https://asciinema.org/a/ID.svg)](https://asciinema.org/a/ID)
```

Откройте README на GitHub и проверьте, что превью ведёт на доступную запись.
На GitHub это кликабельное превью; воспроизведение открывается в asciinema.
