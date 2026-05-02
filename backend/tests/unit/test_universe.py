from datetime import datetime, timezone
from app.data.universe import is_tradable, BTC_USDT


def test_btc_usdt_is_tradable_today() -> None:
    now = datetime.now(timezone.utc)
    assert is_tradable(BTC_USDT, now) is True


def test_unknown_symbol_returns_false() -> None:
    now = datetime.now(timezone.utc)
    assert is_tradable("XXX/YYY", now) is False
