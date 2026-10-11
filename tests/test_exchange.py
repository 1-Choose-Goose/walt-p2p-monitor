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
    def __init__(self, response: FakeResponse | list[FakeResponse]) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


async def no_sleep(_: float) -> None:
    return None


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
async def test_coinbase_success_is_briefly_reused_between_users() -> None:
    session = FakeSession(
        FakeResponse(
            200,
            {"data": {"currency": "USDT", "rates": {"RUB": "85.10"}}},
        )
    )
    client = CoinbaseRatesClient(session=session)

    first = await client.fetch_rub_rate(Asset.USDT)
    second = await client.fetch_rub_rate(Asset.USDT)

    assert first == second == Decimal("85.10")
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_coinbase_client_rejects_invalid_rate() -> None:
    session = FakeSession(
        FakeResponse(200, {"data": {"currency": "BTC", "rates": {"RUB": "0"}}})
    )

    with pytest.raises(ExchangeRateError, match="invalid Coinbase rate"):
        await CoinbaseRatesClient(session=session).fetch_rub_rate(Asset.BTC)


@pytest.mark.asyncio
async def test_coinbase_retries_only_bounded_temporary_statuses() -> None:
    session = FakeSession(
        [
            FakeResponse(503, {}),
            FakeResponse(
                200,
                {"data": {"currency": "USDT", "rates": {"RUB": "85.1"}}},
            ),
        ]
    )

    rate = await CoinbaseRatesClient(session=session, sleep=no_sleep).fetch_rub_rate(
        Asset.USDT
    )

    assert rate == Decimal("85.1")
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_coinbase_does_not_retry_authentication_error() -> None:
    session = FakeSession(FakeResponse(401, {}))

    with pytest.raises(ExchangeRateError, match="HTTP 401"):
        await CoinbaseRatesClient(session=session, sleep=no_sleep).fetch_rub_rate(
            Asset.USDT
        )

    assert len(session.calls) == 1
