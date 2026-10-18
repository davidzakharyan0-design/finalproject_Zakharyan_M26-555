"""Фиксированные котировки только для изолированных тестовых баз."""

from valutatrade_hub.core.constants import DEFAULT_USD_RATES, RATES_FILE
from valutatrade_hub.core.utils import utc_now


def seed_rates(storage):
    """Наполняет временную базу свежим снимком, не вызывая API."""
    stamp = utc_now().isoformat()
    storage.save(
        RATES_FILE,
        {
            "pairs": {
                f"{code}_USD": {"rate": rate, "updated_at": stamp, "source": "Fixture"}
                for code, rate in DEFAULT_USD_RATES.items()
                if code != "USD"
            },
            "last_refresh": stamp,
        },
    )
