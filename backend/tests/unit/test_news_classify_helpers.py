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


# -- 2026-08-20: dynamic_tickers (L9 coverage revival, part b) --------------


def test_dynamic_tickers_matches_a_real_altcoin_not_in_curated_table() -> None:
    """ONDO/KAITO were the operator's own named examples of tickers the
    curated ~24-entry table can never cover -- confirm the dynamic path does."""
    assert extract_affected_assets(
        "ONDO Finance integrates new yield vault",
        dynamic_tickers=frozenset({"ONDO", "KAITO"}),
    ) == ("ONDO",)
    assert extract_affected_assets(
        "KAITO airdrop season two announced",
        dynamic_tickers=frozenset({"ONDO", "KAITO"}),
    ) == ("KAITO",)


def test_dynamic_tickers_merges_with_curated_matches() -> None:
    assert extract_affected_assets(
        "Bitcoin and ONDO both rally",
        dynamic_tickers=frozenset({"ONDO"}),
    ) == ("BTC", "ONDO")


def test_dynamic_tickers_denylist_blocks_common_word_collisions() -> None:
    """CAP/HOME/BANK/etc. are real tickers in the live universe but would
    false-positive on ordinary crypto-news prose if bare-word matched."""
    denylisted = frozenset({"CAP", "HOME", "BANK", "DASH", "RE", "U"})
    assert extract_affected_assets(
        "Total market cap heads back home as banks dash for the exit re: rates",
        dynamic_tickers=denylisted,
    ) == ()


def test_dynamic_tickers_min_length_excludes_short_symbols() -> None:
    """Single/double-letter tickers are excluded even if not explicitly
    denylisted -- too short to trust for bare-word matching."""
    assert extract_affected_assets(
        "U.S. regulators say re the new bill",
        dynamic_tickers=frozenset({"U", "RE"}),
    ) == ()


def test_dynamic_tickers_does_not_override_curated_table() -> None:
    """A ticker already covered by the curated alias table (e.g. BTC) is
    matched via the curated path regardless of whether it's also passed
    in dynamic_tickers -- no double-processing, same result either way."""
    assert extract_affected_assets(
        "Bitcoin surges", dynamic_tickers=frozenset({"BTC"}),
    ) == ("BTC",)


def test_dynamic_tickers_respects_word_boundary() -> None:
    """Same discipline as the curated table's NEAR-vs-Nearby guard: "arb"
    as a prefix inside "arbitrary" must not match the standalone ARB ticker."""
    assert extract_affected_assets(
        "Regulators call the ruling arbitrary and capricious",
        dynamic_tickers=frozenset({"ARB"}),
    ) == ()
    # But a genuine standalone mention still matches.
    assert extract_affected_assets(
        "ARB rallies on Arbitrum news", dynamic_tickers=frozenset({"ARB"}),
    ) == ("ARB",)


def test_dynamic_tickers_empty_default_changes_nothing() -> None:
    """Omitting dynamic_tickers entirely is bit-identical to pre-2026-08-20
    behavior -- every existing call site keeps working unmodified."""
    assert extract_affected_assets("Bitcoin surges past $100k") == ("BTC",)


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
