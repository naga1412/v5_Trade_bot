"""Unit tests for symbol-extraction + category-classifier helpers (SP-9 Phase B3/B4)."""
from __future__ import annotations

import pytest

from app.news.classify_helpers import (
    classify_category,
    extract_affected_assets,
    impact_score_for,
)


@pytest.mark.parametrize("title,expected", [
    ("Bitcoin surges past $100k", ("BTC",)),
    ("Ethereum and Solana lead the rally", ("ETH", "SOL")),
    ("BTC + ETH funding rate flips", ("BTC", "ETH")),
    ("Nearby town festival", ()),                    # NEAR must NOT match nearby
    ("Doge mania returns", ("DOGE",)),
    ("Generic crypto news with no asset", ()),
    ("Ripple settles SEC suit", ("XRP",)),           # 'Ripple' alias → XRP
    ("BNB chain congested", ("BNB",)),
])
def test_extract_affected_assets(title: str, expected: tuple[str, ...]) -> None:
    assert extract_affected_assets(title) == expected


def test_extract_affected_assets_returns_sorted_unique() -> None:
    # Mentions BTC twice + ETH; should dedupe and sort.
    assert extract_affected_assets("BTC and ETH and bitcoin") == ("BTC", "ETH")


def test_extract_affected_assets_handles_empty_string() -> None:
    assert extract_affected_assets("") == ()


@pytest.mark.parametrize("title,expected", [
    ("SEC files lawsuit against Binance", "regulatory"),
    ("Coinbase delists XYZ pair", "exchange"),
    ("Federal Reserve hikes rates", "macro"),
    ("Whale moves 10,000 BTC to cold wallet", "whale"),
    ("Solana launches new feature", "project"),
    ("Reddit thread goes viral on Dogecoin", "social"),
    ("Today is Tuesday", None),
])
def test_classify_category(title: str, expected: str | None) -> None:
    assert classify_category(title) == expected


def test_impact_score_ranges() -> None:
    # Regulatory is highest.
    assert impact_score_for("regulatory", "cryptopanic") > impact_score_for(
        "social", "cryptopanic",
    )
    # Yahoo RSS scaled down vs CryptoPanic.
    assert impact_score_for("regulatory", "yahoo_rss") < impact_score_for(
        "regulatory", "cryptopanic",
    )
    # Bounds.
    assert 0.0 <= impact_score_for(None, "yahoo_rss") <= 1.0
    assert 0.0 <= impact_score_for("regulatory", "cryptopanic") <= 1.0


def test_impact_score_unknown_source_uses_default_modifier() -> None:
    # Unknown source -> 1.0 modifier; macro base = 0.7.
    assert impact_score_for("macro", "unknown_source") == pytest.approx(0.7)


def test_impact_score_unknown_category_uses_neutral_base() -> None:
    # Unknown category -> 0.5 base; cryptopanic modifier = 1.0.
    assert impact_score_for("not-a-real-cat", "cryptopanic") == pytest.approx(0.5)


def test_impact_score_ordering_matches_design() -> None:
    """Per-spec ordering: regulatory > exchange > macro > whale > project > social."""
    s = lambda c: impact_score_for(c, "cryptopanic")  # noqa: E731
    assert s("regulatory") > s("exchange") > s("macro") > s("whale") > s("project") > s("social")
