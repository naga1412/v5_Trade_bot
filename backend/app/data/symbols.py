"""Cross-exchange symbol mapping (SP-3 spec §3.5).

Internal canonical form is "BASE/QUOTE" with slash separator, uppercase
(e.g., "BTC/USDT", "EUR/USD"). Stocks and indices are bare uppercase
identifiers without slash (e.g., "AAPL", "DXY").

`to_native(exchange, canonical) -> str` translates canonical to the
exchange's native form. `from_native(exchange, native) -> str` is the
inverse. `is_supported(exchange, canonical) -> bool` returns False without
raising for symbols an adapter can't fetch.
"""
from __future__ import annotations

from typing import Literal


Exchange = Literal["binance", "bybit", "yahoo", "twelvedata"]
_KNOWN_EXCHANGES: frozenset[str] = frozenset(
    ("binance", "bybit", "yahoo", "twelvedata"),
)


class UnsupportedExchangeError(KeyError):
    """`exchange` is not one of the supported four."""


class UnknownSymbolError(KeyError):
    """`canonical` cannot be resolved to a native form on `exchange`."""


# --- Per-exchange explicit overrides ---


# Yahoo: indices, currencies, futures use suffixes (DXY -> DX-Y.NYB,
# Gold -> GC=F, S&P 500 -> ^GSPC, etc.). The override dict catches the
# common ones; everything else falls through the heuristic.
_YAHOO_CANONICAL_TO_NATIVE: dict[str, str] = {
    "DXY": "DX-Y.NYB",
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "VIX": "^VIX",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "OIL": "CL=F",
}
_YAHOO_NATIVE_TO_CANONICAL: dict[str, str] = {
    v: k for k, v in _YAHOO_CANONICAL_TO_NATIVE.items()
}

# Yahoo stock whitelist: symbols that pass through unchanged (no slash,
# not in the override dict). Listed explicitly so we don't accidentally
# accept arbitrary garbage as a Yahoo ticker.
_YAHOO_STOCK_WHITELIST: frozenset[str] = frozenset((
    "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "TSLA", "NVDA",
    "SPY", "QQQ", "GLD", "SLV", "USO", "TLT", "IWM", "DIA",
))


def _norm(s: str) -> str:
    return (s or "").strip().upper()


def to_native(exchange: str, canonical: str) -> str:
    """Translate a canonical symbol to its native form on `exchange`."""
    ex = exchange.lower().strip()
    if ex not in _KNOWN_EXCHANGES:
        raise UnsupportedExchangeError(exchange)
    sym = _norm(canonical)

    if ex == "binance" or ex == "bybit":
        # Crypto: drop slash, uppercase. Reject obviously-non-crypto symbols.
        if "/" not in sym:
            raise UnknownSymbolError(f"{canonical} not a crypto pair")
        return sym.replace("/", "")

    if ex == "yahoo":
        if sym in _YAHOO_CANONICAL_TO_NATIVE:
            return _YAHOO_CANONICAL_TO_NATIVE[sym]
        if "/" in sym:
            base, quote = sym.split("/", 1)
            # Crypto -> -USD; stablecoins coalesce to USD.
            if quote in ("USDT", "USDC", "BUSD", "FDUSD", "DAI"):
                return f"{base}-USD"
            # FX -> =X
            return f"{base}{quote}=X"
        if sym in _YAHOO_STOCK_WHITELIST:
            return sym
        raise UnknownSymbolError(f"{canonical} not in Yahoo override map")

    if ex == "twelvedata":
        if "/" in sym:
            base, quote = sym.split("/", 1)
            # FX keeps slash; crypto pairs are not on TwelveData free tier.
            if quote in ("USDT", "USDC", "BUSD", "FDUSD"):
                raise UnknownSymbolError(
                    f"crypto {canonical} not supported on TwelveData free tier"
                )
            return sym
        # Stocks / indices pass through.
        return sym

    raise UnsupportedExchangeError(exchange)  # pragma: no cover — defensive


def from_native(exchange: str, native: str) -> str:
    """Inverse of `to_native`."""
    ex = exchange.lower().strip()
    if ex not in _KNOWN_EXCHANGES:
        raise UnsupportedExchangeError(exchange)
    n = _norm(native)

    if ex in ("binance", "bybit"):
        for quote in ("USDT", "USDC", "BUSD", "FDUSD"):
            if n.endswith(quote):
                return f"{n[:-len(quote)]}/{quote}"
        return n

    if ex == "yahoo":
        if n in _YAHOO_NATIVE_TO_CANONICAL:
            return _YAHOO_NATIVE_TO_CANONICAL[n]
        if n.endswith("=X"):
            base_quote = n[:-2]
            # Yahoo FX is BASE+QUOTE concatenated with no separator —
            # heuristic split: 3+3.
            if len(base_quote) == 6:
                return f"{base_quote[:3]}/{base_quote[3:]}"
            return base_quote
        if "-" in n:
            base, quote = n.split("-", 1)
            # USD on Yahoo crypto -> USDT canonical
            if quote == "USD":
                return f"{base}/USDT"
            return f"{base}/{quote}"
        return n

    if ex == "twelvedata":
        return n  # canonical == native for TD

    raise UnsupportedExchangeError(exchange)  # pragma: no cover


def is_supported(exchange: str, canonical: str) -> bool:
    """Return True if `to_native` would succeed for this pair."""
    try:
        to_native(exchange, canonical)
        return True
    except (UnknownSymbolError, UnsupportedExchangeError):
        return False
