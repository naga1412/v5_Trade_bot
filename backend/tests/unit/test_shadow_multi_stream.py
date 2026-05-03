import pytest

from app.shadow.multi_stream import build_combined_stream_url


def test_combined_stream_url_lowercase_and_joined() -> None:
    url = build_combined_stream_url(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframe="1h",
        base="wss://fstream.binance.com",
    )
    assert url == "wss://fstream.binance.com/stream?streams=btcusdt@kline_1h/ethusdt@kline_1h/solusdt@kline_1h"


def test_combined_stream_url_empty_symbols_raises() -> None:
    with pytest.raises(ValueError):
        build_combined_stream_url(symbols=[], timeframe="1h")


def test_combined_stream_url_chunked_at_200_streams() -> None:
    """Binance limit is ~1024 streams per connection but Path length matters."""
    symbols = [f"SYM{i}USDT" for i in range(250)]
    url = build_combined_stream_url(symbols=symbols, timeframe="1h", max_streams=200)
    assert url.count("/") - 2 == 200  # 200 streams in URL (subtract scheme //)
