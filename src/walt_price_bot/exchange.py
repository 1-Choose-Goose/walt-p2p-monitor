from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from .models import Asset

COINBASE_RATES_URL = "https://api.coinbase.com/v2/exchange-rates"


class ExchangeRateError(RuntimeError):
    pass


class CoinbaseRatesClient:
    def __init__(self, *, session: Any) -> None:
        self._session = session

    async def fetch_rub_rate(self, asset: Asset) -> Decimal:
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with self._session.get(
                COINBASE_RATES_URL,
                params={"currency": asset.value},
                headers={"accept": "application/json"},
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ExchangeRateError(f"Coinbase API HTTP {response.status}")
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ExchangeRateError("Coinbase API temporarily unavailable") from exc

        try:
            data = payload["data"]
            if data["currency"] != asset.value:
                raise ExchangeRateError("unexpected Coinbase currency")
            rate = Decimal(str(data["rates"]["RUB"]))
        except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
            raise ExchangeRateError("invalid Coinbase rate") from exc
        if not rate.is_finite() or rate <= 0:
            raise ExchangeRateError("invalid Coinbase rate")
        return rate
