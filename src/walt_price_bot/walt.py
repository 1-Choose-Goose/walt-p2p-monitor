import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from .models import Asset, P2PAd, QuoteSide

WALT_API_URL = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"
RETRYABLE_STATUSES = {429, 503}


class WaltAPIError(RuntimeError):
    pass


class WaltClient:
    def __init__(
        self,
        *,
        session: Any,
        api_key: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = 3,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._sleep = sleep
        self._max_attempts = max_attempts

    async def fetch_ads(self, asset: Asset, side: QuoteSide) -> list[P2PAd]:
        payload = {
            "cryptoCurrency": asset.value,
            "fiatCurrency": "RUB",
            "side": side.value,
            "page": 1,
            "pageSize": 50,
        }
        headers = {"X-API-Key": self._api_key, "accept": "application/json"}
        timeout = aiohttp.ClientTimeout(total=10)
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                async with self._session.post(
                    WALT_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                    allow_redirects=False,
                ) as response:
                    if response.status == 200:
                        return self._parse_response(await response.json(), asset, side)
                    error = WaltAPIError(f"Walt API HTTP {response.status}")
                    if response.status not in RETRYABLE_STATUSES:
                        raise error
                    last_error = error
            except (aiohttp.ClientError, TimeoutError) as exc:
                last_error = exc

            if attempt < self._max_attempts:
                await self._sleep(float(2 ** (attempt - 1)))

        raise WaltAPIError("Walt API temporarily unavailable") from last_error

    @staticmethod
    def _parse_response(payload: object, asset: Asset, side: QuoteSide) -> list[P2PAd]:
        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
            raise WaltAPIError("invalid Walt response status")
        data = payload.get("data")
        if not isinstance(data, list):
            raise WaltAPIError("invalid Walt response data")
        return [WaltClient._parse_ad(item, asset, side) for item in data]

    @staticmethod
    def _parse_ad(item: object, asset: Asset, side: QuoteSide) -> P2PAd:
        if not isinstance(item, dict):
            raise WaltAPIError("invalid Walt ad")
        if item.get("cryptoCurrency") != asset.value:
            raise WaltAPIError("unexpected asset in Walt response")
        if item.get("fiatCurrency") != "RUB":
            raise WaltAPIError("unexpected fiat currency in Walt response")
        if item.get("side") != side.value:
            raise WaltAPIError("unexpected side in Walt response")
        try:
            price = Decimal(str(item["price"]))
            min_amount = Decimal(str(item["minAmount"]))
            max_amount = Decimal(str(item["maxAmount"]))
            rating_value = item.get("executeRate")
            rating = Decimal(str(rating_value)) if rating_value is not None else None
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise WaltAPIError("invalid numeric field in Walt ad") from exc
        if price <= 0 or min_amount < 0 or max_amount < min_amount:
            raise WaltAPIError("invalid amount range in Walt ad")
        payments = item.get("payments", [])
        if not isinstance(payments, list) or not all(isinstance(value, str) for value in payments):
            raise WaltAPIError("invalid payments in Walt ad")
        try:
            identifier = str(item["id"])
            nickname = str(item["nickname"])
            number = str(item["number"])
        except KeyError as exc:
            raise WaltAPIError("missing Walt ad identity") from exc
        return P2PAd(
            id=identifier,
            nickname=nickname,
            asset=asset,
            side=side,
            price=price,
            min_amount=min_amount,
            max_amount=max_amount,
            payments=tuple(payments),
            rating=rating,
            is_online=item.get("isOnline") is True,
            number=number,
        )
