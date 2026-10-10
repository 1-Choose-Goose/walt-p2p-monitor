from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from walt_price_bot.models import (
    BTC,
    ETH,
    Asset,
    AssetCatalogEntry,
    MarketFilters,
    P2PAd,
    QuoteSide,
)
from walt_price_bot.runtime import MarketService


def ad(
    asset: Asset,
    side: QuoteSide,
    price: str,
    *,
    minimum: str = "500",
    payments=("sbp",),
) -> P2PAd:
    return P2PAd(
        id=price,
        user_id="1",
        nickname="seller",
        asset=asset,
        side=side,
        price=Decimal(price),
        min_amount=Decimal(minimum),
        max_amount=Decimal("100000"),
        payments=payments,
        rating=None,
        is_online=True,
    )


@pytest.mark.asyncio
async def test_selected_dynamic_assets_provider_codes_and_filters() -> None:
    entries = (
        AssetCatalogEntry(BTC, "BTC", "BTC", sort_order=0),
        AssetCatalogEntry(ETH, "WETH", None, sort_order=1),
    )
    client = AsyncMock()

    async def fetch_ads(entry, side):
        if side is QuoteSide.SELL:
            return [
                ad(entry.symbol, side, "100", minimum="100", payments=("cash",)),
                ad(entry.symbol, side, "101", minimum="1000", payments=("sbp",)),
            ]
        return [ad(entry.symbol, side, "99", minimum="1000", payments=("sbp",))]

    client.fetch_ads.side_effect = fetch_ads
    exchange = AsyncMock()
    exchange.fetch_rub_rate.return_value = Decimal("100")
    filters = MarketFilters(
        directions=(QuoteSide.BUY,),
        min_limit_from=Decimal("500"),
        min_limit_to=Decimal("2000"),
        desired_amount=Decimal("5000"),
        buy_payments=("sbp",),
        sell_payments=("any",),
    )

    snapshots = await MarketService(client, exchange).get_snapshots(entries, filters)

    assert [snapshot.asset for snapshot in snapshots] == [BTC, ETH]
    assert all(snapshot.buy.price == Decimal("101") for snapshot in snapshots)
    assert all(snapshot.sell is None for snapshot in snapshots)
    assert client.fetch_ads.await_args_list[0].args[0].walt_code == "BTC"
    assert snapshots[1].exchange_price is None


@pytest.mark.asyncio
async def test_unavailable_exchange_keeps_walt_offer() -> None:
    entry = AssetCatalogEntry(BTC, "BTC", "BTC")
    client = AsyncMock()
    client.fetch_ads.side_effect = lambda _, side: [
        ad(BTC, side, "100", minimum="1000")
    ]
    exchange = AsyncMock(side_effect=RuntimeError("offline"))
    exchange.fetch_rub_rate.side_effect = RuntimeError("offline")

    snapshot = (await MarketService(client, exchange).get_snapshots([entry], MarketFilters()))[0]

    assert snapshot.buy is not None
    assert snapshot.sell is not None
    assert snapshot.exchange_price is None
