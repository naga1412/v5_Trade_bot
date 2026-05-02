"""Point-in-time universe (§5.2).

SP-0 hardcodes BTC/USDT only. SP-3 will populate from a `universe_history`
table fetched from exchange listings APIs.
"""
from datetime import datetime

BTC_USDT: str = "BTC/USDT"

_SP0_HARDCODED = {
    BTC_USDT: (datetime(2017, 8, 17, tzinfo=None),),  # listed_at; no delisted_at
}


def is_tradable(symbol: str, ts: datetime) -> bool:
    entry = _SP0_HARDCODED.get(symbol)
    if entry is None:
        return False
    listed_at = entry[0]
    return ts.replace(tzinfo=None) >= listed_at
