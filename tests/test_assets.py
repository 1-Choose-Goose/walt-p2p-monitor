import pytest

from walt_price_bot.models import (
    BTC,
    ETH,
    GRAM,
    NOT,
    SEED_ASSETS,
    USDT,
    AccessStatus,
    Asset,
    AssetCatalogEntry,
    UserPreferences,
    UserRole,
)


def test_asset_normalizes_and_compares_by_ticker() -> None:
    assert Asset(" usdt ") == USDT
    assert Asset("usdt").value == "USDT"
    assert {Asset("btc"), Asset("BTC")} == {BTC}


def test_asset_accepts_future_catalog_ticker_without_code_change() -> None:
    asset = Asset("xrp")

    assert asset.symbol == "XRP"
    assert asset.value == "XRP"


@pytest.mark.parametrize("ticker", ["", " ", "биткоин", "BTC/USDT", "A-B", "A" * 17])
def test_asset_rejects_malformed_ticker(ticker: str) -> None:
    with pytest.raises(ValueError, match="ticker"):
        Asset(ticker)


def test_seed_catalog_contains_every_current_walt_p2p_asset() -> None:
    assert [entry.symbol for entry in SEED_ASSETS] == [GRAM, USDT, BTC, NOT, ETH]
    assert [entry.sort_order for entry in SEED_ASSETS] == list(range(5))
    assert all(entry.active for entry in SEED_ASSETS)


def test_gram_keeps_display_ticker_separate_from_provider_codes() -> None:
    gram = SEED_ASSETS[0]

    assert gram == AssetCatalogEntry(
        symbol=GRAM,
        walt_code="TON",
        exchange_code="TON",
        active=True,
        sort_order=0,
    )
    assert gram.symbol.value == "GRAM"


def test_new_user_preferences_select_all_seed_assets() -> None:
    preferences = UserPreferences()

    assert preferences.assets == tuple(entry.symbol for entry in SEED_ASSETS)
    assert preferences.summary_enabled is False
    assert preferences.summary_interval_minutes == 60
    assert preferences.timezone == "Asia/Yekaterinburg"


def test_access_enums_use_stable_database_values() -> None:
    assert [role.value for role in UserRole] == ["ADMIN", "USER", "PREMIUM"]
    assert [status.value for status in AccessStatus] == [
        "PENDING",
        "AWAITING_TOKEN",
        "ACTIVE",
        "REJECTED",
        "REVOKED",
    ]
