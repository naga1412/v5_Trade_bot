"""Symbol-extraction + category-classifier helpers (SP-9 Phase B3/B4).

Lookup-based, deterministic, no ML. The FinBERT layer (Phase C) handles
the sentiment dimension; here we only handle the structural metadata
fields (which assets are mentioned + which loose category the headline
falls into).
"""
from __future__ import annotations

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


def extract_affected_assets(title: str) -> tuple[str, ...]:
    """Return sorted unique base tickers mentioned in `title`."""
    found: set[str] = set()
    for pat, ticker in _ALIAS_PATTERNS:
        if pat.search(title):
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
