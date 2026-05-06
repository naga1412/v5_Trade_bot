"""alternative.me Fear & Greed Index fetcher (SP-9 Phase D1).

Public surface
--------------
* :class:`FngResult` — frozen dataclass holding the parsed value, label, and
  timestamp returned by ``https://api.alternative.me/fng/``.
* :func:`get_fear_greed_index` — async helper that fetches the index and
  caches the result for one hour in module-level state.

The cache is a tiny ``(time.time(), FngResult)`` tuple kept in module scope
so every importer of this module sees the same cached value. This is fine
for the single-process FastAPI app — distributed callers should hit the
endpoint themselves rather than rely on this helper.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import httpx


_FNG_URL = "https://api.alternative.me/fng/"
_CACHE_TTL: float = 3600.0  # 1 hour
_HTTP_TIMEOUT: float = 10.0

# Module-level cache: ``(epoch_seconds_at_fetch, result)``. ``None`` until
# the first successful fetch — failed fetches do NOT poison this.
_cache: tuple[float, "FngResult"] | None = None


# alternative.me's published label vocabulary. Kept as a Literal so callers
# with mypy enabled get autocomplete + spell-check on the label string.
FngLabel = Literal[
    "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed",
]


@dataclass(frozen=True)
class FngResult:
    """A single Fear & Greed Index reading.

    Attributes
    ----------
    value:
        The 0–100 index score. 0 = extreme fear, 100 = extreme greed.
    label:
        alternative.me's human-readable bucket for ``value``.
    timestamp:
        UTC timestamp the upstream API associated with this reading.
    """

    value: int
    label: str
    timestamp: datetime


async def get_fear_greed_index() -> FngResult:
    """Fetch the alternative.me F&G index, cached 1h in module state.

    Cache hits within :data:`_CACHE_TTL` seconds short-circuit the HTTP
    call and return the same :class:`FngResult` instance. Network or
    HTTP errors propagate as :class:`httpx.HTTPError` and leave the
    cache untouched so the next caller will retry.
    """
    global _cache
    now = time.time()
    if _cache is not None and (now - _cache[0]) < _CACHE_TTL:
        return _cache[1]

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(_FNG_URL)
        resp.raise_for_status()
        data = resp.json()["data"][0]

    result = FngResult(
        value=int(data["value"]),
        label=str(data["value_classification"]),
        timestamp=datetime.fromtimestamp(int(data["timestamp"]), tz=timezone.utc),
    )
    _cache = (now, result)
    return result


__all__ = ["FngLabel", "FngResult", "get_fear_greed_index"]
