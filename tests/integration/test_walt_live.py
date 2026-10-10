import os

import aiohttp
import pytest

from walt_price_bot.models import Asset, QuoteSide
from walt_price_bot.walt import WaltClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_walt_supports_required_rub_markets() -> None:
    api_key = os.environ.get("WALT_API_KEY")
    if not api_key:
        pytest.skip("WALT_API_KEY is not set")

    async with aiohttp.ClientSession() as session:
        client = WaltClient(session=session, api_key=api_key)
        for asset in Asset:
            for side in QuoteSide:
                ads = await client.fetch_ads(asset, side)
                assert ads
                assert all(ad.asset is asset and ad.side is side for ad in ads)
