from typing import Any

import pytest
from pydantic import SecretStr

from walt_price_bot.models import SEED_ASSETS, Asset, QuoteSide
from walt_price_bot.walt import (
    TokenValidationStatus,
    WaltAPIError,
    WaltClient,
    validate_walt_token,
)

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


class BrokenJsonResponse(FakeResponse):
    async def json(self) -> object:
        raise ValueError("invalid json")


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
async def test_fetch_ads_retries_rate_limit_without_unbounded_loop() -> None:
    session = FakeSession(
        [
            FakeResponse(429, {"status": "ERROR"}),
            FakeResponse(200, {"status": "SUCCESS", "data": [VALID_AD]}),
        ]
    )

    ads = await WaltClient(
        session=session, api_key="secret-key", sleep=no_sleep
    ).fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert len(ads) == 1
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_invalid_json_is_source_error_not_empty_market() -> None:
    session = FakeSession([BrokenJsonResponse(200, None)])

    with pytest.raises(WaltAPIError, match="temporarily unavailable"):
        await WaltClient(
            session=session,
            api_key="secret-key",
            sleep=no_sleep,
            max_attempts=1,
        ).fetch_ads(Asset.USDT, QuoteSide.SELL)


@pytest.mark.asyncio
async def test_fetch_ads_rejects_malformed_or_wrong_currency_data() -> None:
    malformed = dict(VALID_AD, cryptoCurrency="TON")
    session = FakeSession([FakeResponse(200, {"status": "SUCCESS", "data": [malformed]})])
    client = WaltClient(session=session, api_key="secret-key", sleep=no_sleep)

    with pytest.raises(WaltAPIError, match="unexpected asset"):
        await client.fetch_ads(Asset.USDT, QuoteSide.SELL)


@pytest.mark.asyncio
async def test_one_malformed_ad_does_not_hide_valid_page_offers() -> None:
    malformed = dict(VALID_AD, price="NaN")
    valid = dict(VALID_AD, id="valid")
    session = FakeSession(
        [FakeResponse(200, {"status": "SUCCESS", "data": [malformed, valid]})]
    )

    ads = await WaltClient(
        session=session, api_key="secret-key", sleep=no_sleep
    ).fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert [ad.id for ad in ads] == ["valid"]


@pytest.mark.asyncio
async def test_non_list_walt_data_is_not_treated_as_empty_market() -> None:
    session = FakeSession(
        [FakeResponse(200, {"status": "SUCCESS", "data": {"id": "bad"}})]
    )

    with pytest.raises(WaltAPIError, match="invalid Walt response data"):
        await WaltClient(
            session=session, api_key="secret-key", sleep=no_sleep
        ).fetch_ads(Asset.USDT, QuoteSide.SELL)


@pytest.mark.asyncio
async def test_fetch_ads_does_not_retry_bad_request() -> None:
    session = FakeSession([FakeResponse(400, {"status": "ERROR"})])
    client = WaltClient(session=session, api_key="secret-key", sleep=no_sleep)

    with pytest.raises(WaltAPIError, match="HTTP 400"):
        await client.fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert len(session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", "NaN"),
        ("price", "Infinity"),
        ("price", "0"),
        ("price", "-1"),
        ("minAmount", "NaN"),
        ("maxAmount", "Infinity"),
        ("minAmount", "2000"),
    ],
)
async def test_fetch_ads_rejects_invalid_numeric_values(field: str, value: str) -> None:
    item = dict(VALID_AD, **{field: value})
    if field == "minAmount" and value == "2000":
        item["maxAmount"] = "1000"
    session = FakeSession([FakeResponse(200, {"status": "SUCCESS", "data": [item]})])
    client = WaltClient(session=session, api_key="secret-key", sleep=no_sleep)

    with pytest.raises(WaltAPIError, match="invalid"):
        await client.fetch_ads(Asset.USDT, QuoteSide.SELL)


@pytest.mark.asyncio
async def test_fetch_ads_uses_bounded_pagination() -> None:
    first_page = [dict(VALID_AD, id=str(index)) for index in range(50)]
    second_page = [dict(VALID_AD, id="last")]
    session = FakeSession(
        [
            FakeResponse(200, {"status": "SUCCESS", "data": first_page}),
            FakeResponse(200, {"status": "SUCCESS", "data": second_page}),
        ]
    )

    ads = await WaltClient(
        session=session, api_key="secret-key", sleep=no_sleep
    ).fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert len(ads) == 51
    assert [call["json"]["page"] for call in session.calls] == [1, 2]


@pytest.mark.asyncio
async def test_invalid_ad_on_full_page_does_not_stop_pagination() -> None:
    first_page = [dict(VALID_AD, id=str(index)) for index in range(49)]
    first_page.append(dict(VALID_AD, id="bad", price="NaN"))
    second_page = [dict(VALID_AD, id="last")]
    session = FakeSession(
        [
            FakeResponse(200, {"status": "SUCCESS", "data": first_page}),
            FakeResponse(200, {"status": "SUCCESS", "data": second_page}),
        ]
    )

    ads = await WaltClient(
        session=session, api_key="secret-key", sleep=no_sleep
    ).fetch_ads(Asset.USDT, QuoteSide.SELL)

    assert len(ads) == 50
    assert [call["json"]["page"] for call in session.calls] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_token_validation_rejects_auth_errors_without_retry(status: int) -> None:
    session = FakeSession([FakeResponse(status, {"status": "ERROR"})])

    result = await validate_walt_token(
        session, SecretStr("secret-token"), SEED_ASSETS[1], sleep=no_sleep
    )

    assert result.status is TokenValidationStatus.INVALID
    assert len(session.calls) == 1
    assert "secret-token" not in repr(result)


@pytest.mark.asyncio
async def test_token_validation_retries_transient_failure() -> None:
    session = FakeSession(
        [
            FakeResponse(503, {"status": "ERROR"}),
            FakeResponse(200, {"status": "SUCCESS", "data": []}),
        ]
    )

    result = await validate_walt_token(
        session, SecretStr("secret-token"), SEED_ASSETS[1], sleep=no_sleep
    )

    assert result.status is TokenValidationStatus.VALID
    assert len(session.calls) == 2
    assert session.calls[0]["json"]["cryptoCurrency"] == "USDT"
