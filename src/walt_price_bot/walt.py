import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

import aiohttp
from pydantic import SecretStr

from .models import Asset, AssetCatalogEntry, P2PAd, QuoteSide

WALT_API_URL = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"
RETRYABLE_STATUSES = {429, 503}


class WaltAPIError(RuntimeError):
    pass


class TokenValidationStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    TRANSIENT = "TRANSIENT"


@dataclass(frozen=True, slots=True)
class TokenValidationResult:
    status: TokenValidationStatus


async def validate_walt_token(
    session: Any,
    token: SecretStr,
    probe_asset: AssetCatalogEntry,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_attempts: int = 3,
) -> TokenValidationResult:
    """Validate a token without ever returning or logging its value."""

    payload = {
        "cryptoCurrency": probe_asset.walt_code,
        "fiatCurrency": "RUB",
        "side": QuoteSide.SELL.value,
        "page": 1,
        "pageSize": 1,
    }
    headers = {
        "X-API-Key": token.get_secret_value(),
        "accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=10)
    for attempt in range(1, max_attempts + 1):
        try:
            async with session.post(
                WALT_API_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                if response.status in {401, 403}:
                    return TokenValidationResult(TokenValidationStatus.INVALID)
                if response.status == 200:
                    body = await response.json()
                    if isinstance(body, dict) and body.get("status") == "SUCCESS":
                        return TokenValidationResult(TokenValidationStatus.VALID)
                if response.status not in RETRYABLE_STATUSES:
                    return TokenValidationResult(TokenValidationStatus.TRANSIENT)
        except (aiohttp.ClientError, TimeoutError):
            pass
        if attempt < max_attempts:
            await sleep(float(2 ** (attempt - 1)))
    return TokenValidationResult(TokenValidationStatus.TRANSIENT)


class WaltClient:
    def __init__(
        self,
        *,
        session: Any,
        api_key: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = 3,
        max_pages: int = 3,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._max_pages = max_pages

    async def fetch_ads(
        self, asset: AssetCatalogEntry | Asset, side: QuoteSide
    ) -> list[P2PAd]:
        entry = (
            asset
            if isinstance(asset, AssetCatalogEntry)
            else AssetCatalogEntry(asset, asset.value, asset.value)
        )
        ads: list[P2PAd] = []
        for page in range(1, self._max_pages + 1):
            page_ads = await self._fetch_page(entry, side, page)
            ads.extend(page_ads)
            if len(page_ads) < 50:
                break
        return ads

    async def _fetch_page(
        self, asset: AssetCatalogEntry, side: QuoteSide, page: int
    ) -> list[P2PAd]:
        payload = {
            "cryptoCurrency": asset.walt_code,
            "fiatCurrency": "RUB",
            "side": side.value,
            "page": page,
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
    def _parse_response(
        payload: object, asset: AssetCatalogEntry, side: QuoteSide
    ) -> list[P2PAd]:
        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
            raise WaltAPIError("invalid Walt response status")
        data = payload.get("data")
        if not isinstance(data, list):
            raise WaltAPIError("invalid Walt response data")
        return [WaltClient._parse_ad(item, asset, side) for item in data]

    @staticmethod
    def _parse_ad(
        item: object, asset: AssetCatalogEntry, side: QuoteSide
    ) -> P2PAd:
        if not isinstance(item, dict):
            raise WaltAPIError("invalid Walt ad")
        if item.get("cryptoCurrency") != asset.walt_code:
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
        numbers = (price, min_amount, max_amount)
        if (
            not all(value.is_finite() for value in numbers)
            or price <= 0
            or min_amount < 0
            or max_amount < min_amount
            or (rating is not None and (not rating.is_finite() or rating < 0))
        ):
            raise WaltAPIError("invalid amount range in Walt ad")
        payments = item.get("payments", [])
        if not isinstance(payments, list) or not all(isinstance(value, str) for value in payments):
            raise WaltAPIError("invalid payments in Walt ad")
        try:
            identifier = str(item["id"])
            user_id = str(item["userId"])
            nickname = str(item["nickname"])
            number = str(item["number"])
        except KeyError as exc:
            raise WaltAPIError("missing Walt ad identity") from exc
        return P2PAd(
            id=identifier,
            user_id=user_id,
            nickname=nickname,
            asset=asset.symbol,
            side=side,
            price=price,
            min_amount=min_amount,
            max_amount=max_amount,
            payments=tuple(payments),
            rating=rating,
            is_online=item.get("isOnline") is True,
            number=number,
        )
