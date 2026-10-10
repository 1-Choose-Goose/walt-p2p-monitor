import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from .models import Asset, AssetCatalogEntry

COINBASE_RATES_URL = "https://api.coinbase.com/v2/exchange-rates"
RETRYABLE_STATUSES = {429, 503}


class ExchangeRateError(RuntimeError):
    pass


class CoinbaseRatesClient:
    def __init__(
        self,
        *,
        session: Any,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = 3,
    ) -> None:
        self._session = session
        self._sleep = sleep
        self._max_attempts = max_attempts

    async def fetch_rub_rate(self, asset: AssetCatalogEntry | Asset) -> Decimal:
        code = asset.exchange_code if isinstance(asset, AssetCatalogEntry) else asset.value
        if code is None:
            raise ExchangeRateError("exchange rate is not configured")
        timeout = aiohttp.ClientTimeout(total=10)
        last_error: Exception | None = None
        payload: object | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with self._session.get(
                    COINBASE_RATES_URL,
                    params={"currency": code},
                    headers={"accept": "application/json"},
                    timeout=timeout,
                    allow_redirects=False,
                ) as response:
                    if response.status == 200:
                        payload = await response.json()
                        break
                    error = ExchangeRateError(
                        f"Coinbase API HTTP {response.status}"
                    )
                    if response.status not in RETRYABLE_STATUSES:
                        raise error
                    last_error = error
            except (aiohttp.ClientError, TimeoutError) as exc:
                last_error = exc
            if attempt < self._max_attempts:
                await self._sleep(float(2 ** (attempt - 1)))
        else:
            raise ExchangeRateError(
                "Coinbase API temporarily unavailable"
            ) from last_error

        try:
            if not isinstance(payload, dict):
                raise TypeError
            data = payload["data"]
            if data["currency"] != code:
                raise ExchangeRateError("unexpected Coinbase currency")
            rate = Decimal(str(data["rates"]["RUB"]))
        except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
            raise ExchangeRateError("invalid Coinbase rate") from exc
        if not rate.is_finite() or rate <= 0:
            raise ExchangeRateError("invalid Coinbase rate")
        return rate
