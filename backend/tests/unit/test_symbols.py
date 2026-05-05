import pytest

from app.data.symbols import (
    UnknownSymbolError,
    UnsupportedExchangeError,
    from_native,
    is_supported,
    to_native,
)


# --- to_native ---


@pytest.mark.parametrize("canonical, expected", [
    ("BTC/USDT", "BTCUSDT"),
    ("ETH/USDT", "ETHUSDT"),
    ("SOL/USDC", "SOLUSDC"),
])
def test_to_native_binance_drops_slash(canonical: str, expected: str) -> None:
    assert to_native("binance", canonical) == expected


@pytest.mark.parametrize("canonical, expected", [
    ("BTC/USDT", "BTCUSDT"),
    ("ETH/USDT", "ETHUSDT"),
])
def test_to_native_bybit_drops_slash(canonical: str, expected: str) -> None:
    assert to_native("bybit", canonical) == expected


@pytest.mark.parametrize("canonical, expected", [
    ("BTC/USDT", "BTC-USD"),     # USDT mapped to USD on Yahoo
    ("ETH/USDT", "ETH-USD"),
    ("EUR/USD", "EURUSD=X"),     # FX uses =X suffix
    ("AAPL", "AAPL"),            # stock pass-through
    ("DXY", "DX-Y.NYB"),         # explicit override
])
def test_to_native_yahoo_handles_crypto_fx_stock_index(
    canonical: str, expected: str,
) -> None:
    assert to_native("yahoo", canonical) == expected


@pytest.mark.parametrize("canonical, expected", [
    ("EUR/USD", "EUR/USD"),      # FX keeps slash on TwelveData
    ("AAPL", "AAPL"),            # stock pass-through
])
def test_to_native_twelvedata_keeps_slash_for_fx(
    canonical: str, expected: str,
) -> None:
    assert to_native("twelvedata", canonical) == expected


def test_to_native_lowercase_and_whitespace_normalized() -> None:
    assert to_native("binance", " btc/usdt ") == "BTCUSDT"


def test_to_native_unknown_exchange_raises() -> None:
    with pytest.raises(UnsupportedExchangeError):
        to_native("kraken", "BTC/USDT")


def test_to_native_unknown_symbol_with_no_heuristic_raises() -> None:
    """Yahoo: a symbol with no slash + not in stock pass-through whitelist is unknown."""
    with pytest.raises(UnknownSymbolError):
        to_native("yahoo", "DXY-NOT-IN-MAP")


# --- from_native ---


@pytest.mark.parametrize("native, expected", [
    ("BTCUSDT", "BTC/USDT"),
    ("ETHUSDT", "ETH/USDT"),
    ("SOLUSDC", "SOL/USDC"),
])
def test_from_native_binance(native: str, expected: str) -> None:
    assert from_native("binance", native) == expected


@pytest.mark.parametrize("native, expected", [
    ("BTC-USD", "BTC/USDT"),
    ("EURUSD=X", "EUR/USD"),
    ("AAPL", "AAPL"),
    ("DX-Y.NYB", "DXY"),
])
def test_from_native_yahoo(native: str, expected: str) -> None:
    assert from_native("yahoo", native) == expected


# --- is_supported ---


def test_is_supported_dxy_only_on_yahoo() -> None:
    assert is_supported("yahoo", "DXY") is True
    assert is_supported("binance", "DXY") is False
    assert is_supported("bybit", "DXY") is False


def test_is_supported_btc_usdt_on_crypto_exchanges() -> None:
    assert is_supported("binance", "BTC/USDT") is True
    assert is_supported("bybit", "BTC/USDT") is True
    assert is_supported("yahoo", "BTC/USDT") is True   # via -USD mapping
    assert is_supported("twelvedata", "BTC/USDT") is False  # no crypto mapping
