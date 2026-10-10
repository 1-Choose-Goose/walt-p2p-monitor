from typing import Any

import pytest

from walt_price_bot.models import Asset, QuoteSide
from walt_price_bot.walt import WaltAPIError, WaltClient

VALID_AD = {
    "id": "12345",
    "number": "A1B2C3",
    "userId": 67890,
    "nickname": "CryptoTrader",
    "cryptoCurrency": "USDT",
    "fiatCurrency": "RUB",
    "side": "SELL",
    "price": "98.50",
    "lastQuantity": "1500.00",
    "minAmount": "1000.00",
    "maxAmount": "50000.00",
    "payments": ["sberbank", "tinkoff"],
    "orderNum": 156,
    "executeRate": "0.9875",
    "isOnline": True,
    "merchantLevel": "MERCHANT",
    "paymentPeriod": 15,
    "isAutoAccept": True,
}


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
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


async def no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_fetch_ads_sends_documented_rub_payload_and_parses_decimal() -> None:
    session = FakeSession([FakeResponse(200, {"status": "SUCCESS", "data": [VALID_AD]})])
    client = WaltClient(session=session, api_key="secret-key", sleep=no_sleep)

    ads = await client.fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert session.calls[0]["headers"] == {"X-API-Key": "secret-key", "accept": "application/json"}
    assert session.calls[0]["json"] == {
        "cryptoCurrency": "USDT",
        "fiatCurrency": "RUB",
        "side": "SELL",
        "page": 1,
        "pageSize": 50,
    }
    assert str(ads[0].price) == "98.50"
    assert ads[0].payments == ("sberbank", "tinkoff")
    assert ads[0].number == "A1B2C3"
    assert ads[0].user_id == "67890"


@pytest.mark.asyncio
async def test_fetch_ads_retries_temporary_failure() -> None:
    session = FakeSession(
        [
            FakeResponse(503, {"status": "ERROR"}),
            FakeResponse(200, {"status": "SUCCESS", "data": [VALID_AD]}),
        ]
    )
    client = WaltClient(session=session, api_key="secret-key", sleep=no_sleep)

    ads = await client.fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert len(session.calls) == 2
    assert len(ads) == 1


@pytest.mark.asyncio
async def test_fetch_ads_rejects_malformed_or_wrong_currency_data() -> None:
    malformed = dict(VALID_AD, cryptoCurrency="TON")
    session = FakeSession([FakeResponse(200, {"status": "SUCCESS", "data": [malformed]})])
    client = WaltClient(session=session, api_key="secret-key", sleep=no_sleep)

    with pytest.raises(WaltAPIError, match="unexpected asset"):
        await client.fetch_ads(Asset.USDT, QuoteSide.SELL)


@pytest.mark.asyncio
async def test_fetch_ads_does_not_retry_bad_request() -> None:
    session = FakeSession([FakeResponse(400, {"status": "ERROR"})])
    client = WaltClient(session=session, api_key="secret-key", sleep=no_sleep)

    with pytest.raises(WaltAPIError, match="HTTP 400"):
        await client.fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert len(session.calls) == 1
