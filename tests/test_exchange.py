from decimal import Decimal

import pytest

from walt_price_bot.exchange import CoinbaseRatesClient, ExchangeRateError
from walt_price_bot.models import Asset


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


@pytest.mark.asyncio
async def test_coinbase_client_returns_ruble_exchange_rate() -> None:
    session = FakeSession(
        FakeResponse(
            200,
            {"data": {"currency": "TON", "rates": {"RUB": "124.0761455128"}}},
        )
    )

    rate = await CoinbaseRatesClient(session=session).fetch_rub_rate(Asset.TON)

    assert rate == Decimal("124.0761455128")
    assert session.calls[0]["url"] == "https://api.coinbase.com/v2/exchange-rates"
    assert session.calls[0]["params"] == {"currency": "TON"}


@pytest.mark.asyncio
async def test_coinbase_client_rejects_invalid_rate() -> None:
    session = FakeSession(
        FakeResponse(200, {"data": {"currency": "BTC", "rates": {"RUB": "0"}}})
    )

    with pytest.raises(ExchangeRateError, match="invalid Coinbase rate"):
        await CoinbaseRatesClient(session=session).fetch_rub_rate(Asset.BTC)
