"""Symbol-extraction + category-classifier helpers (SP-9 Phase B3/B4).

Lookup-based, deterministic, no ML. The FinBERT layer (Phase C) handles
the sentiment dimension; here we only handle the structural metadata
fields (which assets are mentioned + which loose category the headline
falls into).
"""
from __future__ import annotations

import functools
import re

# Curated alias table — top ~50 cryptos + common alternates.
# Keys are lower-case alias tokens; values are canonical base tickers.
_ALIAS_TABLE: dict[str, str] = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "ripple": "XRP", "xrp": "XRP",
    "cardano": "ADA", "ada": "ADA",
    "dogecoin": "DOGE", "doge": "DOGE",
    "polkadot": "DOT", "dot": "DOT",
    "avalanche": "AVAX", "avax": "AVAX",
    "polygon": "MATIC", "matic": "MATIC",
    "chainlink": "LINK", "link": "LINK",
    "litecoin": "LTC", "ltc": "LTC",
    "binance coin": "BNB", "bnb": "BNB",
    "shiba": "SHIB", "shib": "SHIB",
    "tron": "TRX", "trx": "TRX",
    "near": "NEAR",
    "cosmos": "ATOM", "atom": "ATOM",
    "uniswap": "UNI", "uni": "UNI",
    "stellar": "XLM", "xlm": "XLM",
    "filecoin": "FIL", "fil": "FIL",
    "aptos": "APT", "apt": "APT",
    "arbitrum": "ARB", "arb": "ARB",
    "optimism": "OP",
    "monero": "XMR", "xmr": "XMR",
    "hedera": "HBAR", "hbar": "HBAR",
}


# Pre-compiled regex per alias for speed (`\b` word boundary, case-insensitive).
_ALIAS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE), ticker)
    for alias, ticker in _ALIAS_TABLE.items()
]


# 2026-08-20 (L9 coverage revival, part b): the curated table above only
# ever covered ~24 of the universe's 70+ symbols -- a static list always
# lags a daily-rotating universe by construction. `dynamic_tickers`
# (the current live trading universe's own ticker set, fetched by the
# caller once per ingest batch -- see app/news/persistence.py) is
# matched directly on its own symbol string, so any headline naming a
# ticker the bot actually trades gets picked up without a code change
# every time the universe rotates. `_ALIAS_TABLE` remains the answer for
# the OTHER direction: cases where the headline uses a project/company
# name that differs from the ticker (Bitcoin->BTC, Ripple->XRP,
# Hyperliquid->HYPE if ever added there) -- a dynamic ticker set can
# never derive that mapping on its own.
#
# Bare-word ticker matching has a real collision risk with common
# English words that a curated alias table (hand-picked, reviewed each
# addition) doesn't: this universe alone contains CAP, DASH, HOME, BANK,
# MET, LIT, MON, TAO, DOS, ACE, RE, U, GPS, ALL -- auto-matching these on
# `\bTICKER\b` would flag most crypto headlines as "about" them (e.g.
# "market cap" -> CAP on every article). `_DYNAMIC_MATCH_DENYLIST` is
# the deliberately small, curated exclusion for tickers identified this
# way; `_MIN_DYNAMIC_TICKER_LEN` additionally drops single/double-letter
# tickers (nothing in the alias table's manual entries is that short,
# by design). Extend the denylist as the coverage measurement (part c)
# surfaces more collisions -- it is a starting set, not exhaustive.
_MIN_DYNAMIC_TICKER_LEN: int = 3

_DYNAMIC_MATCH_DENYLIST: frozenset[str] = frozenset({
    "CAP", "DASH", "HOME", "BANK", "MET", "LIT", "MON", "TAO", "DOS",
    "ACE", "GPS", "ALL", "RE", "U", "MOVE", "LIVE", "TOP", "NEW", "APP",
    "REAL", "FUN", "LAYER", "OPEN", "CORE", "TRUE", "TRUST", "SAFE",
})


@functools.lru_cache(maxsize=512)
def _dynamic_pattern(ticker: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(ticker)}\b", re.IGNORECASE)


def extract_affected_assets(
    title: str, *, dynamic_tickers: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Return sorted unique base tickers mentioned in `title`.

    ``dynamic_tickers``, when supplied, is additionally matched via
    bare-word regex against each ticker not already covered by the
    curated alias table and not in ``_DYNAMIC_MATCH_DENYLIST`` -- see
    the comment above for why both guards exist. Defaults to empty so
    every existing caller (and the persist-time merge in
    ``app/news/persistence.py``) is unaffected until it opts in.
    """
    found: set[str] = set()
    for pat, ticker in _ALIAS_PATTERNS:
        if pat.search(title):
            found.add(ticker)
    _curated_tickers = set(_ALIAS_TABLE.values())
    for ticker in dynamic_tickers:
        if ticker in _curated_tickers or ticker in _DYNAMIC_MATCH_DENYLIST:
            continue
        if len(ticker) < _MIN_DYNAMIC_TICKER_LEN:
            continue
        if _dynamic_pattern(ticker).search(title):
            found.add(ticker)
    return tuple(sorted(found))


_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "regulatory": (
        "sec", "lawsuit", "regulator", "ban", "compliance", "doj", "cftc",
        "court", "fine", "settlement", "subpoena",
    ),
    "exchange": (
        "binance", "coinbase", "kraken", "bybit", "okx", "bitfinex", "delist",
        "listing", "withdrawal", "deposit halt", "outage",
    ),
    "macro": (
        "federal reserve", "fed ", "rate hike", "rate cut", "inflation",
        "cpi", "fomc", "treasury", "dollar", "dxy", "s&p", "equities",
    ),
    "whale": (
        "whale", "cold wallet", "moves ", "transferred", "transfer ",
        "moved ",
    ),
    "project": (
        "launch", "upgrade", "fork", "mainnet", "testnet", "partnership",
        "feature", "v2", "v3", "release",
    ),
    "social": (
        "reddit", "twitter", "x post", "viral", "meme", "tiktok", "hype",
    ),
}


def classify_category(title: str) -> str | None:
    """Best-effort keyword classification → one of the 6 buckets, or None."""
    lower = title.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return cat
    return None


# B4: heuristic 0..1 impact score by (category, source).
# Per spec §4 design intent: regulatory > exchange > macro > whale > project > social.
_BASE_IMPACT: dict[str | None, float] = {
    "regulatory": 1.0,
    "exchange": 0.8,
    "macro": 0.7,
    "whale": 0.6,
    "project": 0.5,
    "social": 0.3,
    None: 0.5,
}

_SOURCE_MODIFIER: dict[str, float] = {
    "cryptopanic": 1.0,    # 'hot' filter already curated.
    "yahoo_rss": 0.85,     # unfiltered firehose → mild discount.
}


def impact_score_for(category: str | None, source: str) -> float:
    """Return a heuristic [0, 1] impact score by (category, source).

    Used by L9 to weight multi-article aggregations toward higher-impact items.
    """
    base = _BASE_IMPACT.get(category, 0.5)
    mod = _SOURCE_MODIFIER.get(source, 1.0)
    return min(1.0, max(0.0, base * mod))
