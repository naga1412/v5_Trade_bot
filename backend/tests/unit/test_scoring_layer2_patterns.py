"""SP-2 Phase E task E1 — L2 pattern aggregator unit tests.

Spec §3.3. The aggregator is pure-Python; we monkeypatch ``ALL_PATTERNS`` so
each test scopes the universe of detectors that "fire". Stats come from an
in-memory ``PatternStatsLookup`` so no DB is touched here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pytest

from app.core.patterns.base import PatternFire
from app.core.scoring.types import Direction


def _bars(n: int = 100) -> pd.DataFrame:
    """Synthetic OHLCV; content is irrelevant since we monkeypatch the patterns list."""
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "open": [100.0] * n, "high": [101.0] * n,
        "low": [99.0] * n, "close": [100.0] * n,
        "volume": [1_000.0] * n,
    }, index=idx)


@dataclass
class _StubPattern:
    pattern_id: str
    pattern_type: str = "candle"
    fire: PatternFire | None = None

    def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
        return self.fire


def test_score_neutral_when_no_patterns_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.scoring import layer2_patterns
    monkeypatch.setattr(layer2_patterns, "ALL_PATTERNS", [])

    stats = layer2_patterns.PatternStatsLookup(by_pattern={})
    score = layer2_patterns.score(_bars(), current_idx=99, stats=stats)

    assert score.direction is Direction.NEUTRAL
    assert score.strength == 0.0


def test_score_long_when_only_bullish_pattern_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.scoring import layer2_patterns
    fire = PatternFire(
        pattern_id="hammer", direction="LONG",
        strength=0.8, confidence=0.7, evidence={},
    )
    monkeypatch.setattr(
        layer2_patterns, "ALL_PATTERNS", [_StubPattern("hammer", fire=fire)],
    )

    stats = layer2_patterns.PatternStatsLookup(by_pattern={"hammer": 0.7})
    score = layer2_patterns.score(_bars(), current_idx=99, stats=stats)

    raw = 0.8 * 0.7 * 0.7  # strength * confidence * accuracy
    expected = math.tanh(raw / 3.0)
    assert score.direction is Direction.LONG
    assert score.strength == pytest.approx(expected, abs=1e-9)


def test_score_short_when_bearish_dominates(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.scoring import layer2_patterns
    bull = PatternFire(
        pattern_id="hammer", direction="LONG",
        strength=0.5, confidence=0.5, evidence={},
    )
    bear = PatternFire(
        pattern_id="hanging_man", direction="SHORT",
        strength=0.9, confidence=0.8, evidence={},
    )
    monkeypatch.setattr(layer2_patterns, "ALL_PATTERNS", [
        _StubPattern("hammer", fire=bull),
        _StubPattern("hanging_man", fire=bear),
    ])
    stats = layer2_patterns.PatternStatsLookup(by_pattern={
        "hammer": 0.6, "hanging_man": 0.7,
    })
    score = layer2_patterns.score(_bars(), current_idx=99, stats=stats)

    assert score.direction is Direction.SHORT


def test_score_uses_prior_when_pattern_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If pattern_id missing from PatternStatsLookup, the prior 0.5 applies."""
    from app.core.scoring import layer2_patterns
    fire = PatternFire(
        pattern_id="new_pattern", direction="LONG",
        strength=1.0, confidence=1.0, evidence={},
    )
    monkeypatch.setattr(
        layer2_patterns, "ALL_PATTERNS",
        [_StubPattern("new_pattern", fire=fire)],
    )
    stats = layer2_patterns.PatternStatsLookup(by_pattern={})
    score = layer2_patterns.score(_bars(), current_idx=99, stats=stats)

    raw = 1.0 * 1.0 * 0.5  # prior accuracy
    expected = math.tanh(raw / 3.0)
    assert score.strength == pytest.approx(expected, abs=1e-9)


def test_enabled_patterns_filter_excludes_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.scoring import layer2_patterns
    fire = PatternFire(
        pattern_id="hammer", direction="LONG",
        strength=0.8, confidence=0.7, evidence={},
    )
    monkeypatch.setattr(
        layer2_patterns, "ALL_PATTERNS", [_StubPattern("hammer", fire=fire)],
    )

    stats = layer2_patterns.PatternStatsLookup(by_pattern={"hammer": 0.7})
    # Empty enabled set means nothing fires through.
    score = layer2_patterns.score(
        _bars(), current_idx=99, stats=stats, enabled_patterns=set(),
    )
    assert score.direction is Direction.NEUTRAL
    assert score.strength == 0.0


def test_score_swallows_pattern_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A buggy pattern must not break the whole layer."""
    from app.core.scoring import layer2_patterns

    class _Exploding:
        pattern_id = "exploding"
        pattern_type = "candle"

        def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
            raise RuntimeError("oops")

    fire = PatternFire(
        pattern_id="hammer", direction="LONG",
        strength=0.9, confidence=0.9, evidence={},
    )
    monkeypatch.setattr(layer2_patterns, "ALL_PATTERNS", [
        _Exploding(),
        _StubPattern("hammer", fire=fire),
    ])
    stats = layer2_patterns.PatternStatsLookup(by_pattern={"hammer": 0.9})
    score = layer2_patterns.score(_bars(), current_idx=99, stats=stats)
    assert score.direction is Direction.LONG


def test_pattern_stats_lookup_get_returns_prior_when_missing() -> None:
    from app.core.scoring.layer2_patterns import PRIOR_ACCURACY, PatternStatsLookup
    lookup = PatternStatsLookup(by_pattern={"hammer": 0.65})
    assert lookup.get("hammer") == pytest.approx(0.65)
    assert lookup.get("totally_unknown") == pytest.approx(PRIOR_ACCURACY)


def test_confidence_caps_at_one_when_many_patterns_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.scoring import layer2_patterns
    fires = [
        PatternFire(
            pattern_id=f"p{i}", direction="LONG",
            strength=0.1, confidence=0.1, evidence={},
        )
        for i in range(15)
    ]
    monkeypatch.setattr(layer2_patterns, "ALL_PATTERNS", [
        _StubPattern(f"p{i}", fire=fires[i]) for i in range(15)
    ])
    stats = layer2_patterns.PatternStatsLookup(
        by_pattern={f"p{i}": 0.5 for i in range(15)},
    )
    score = layer2_patterns.score(_bars(), current_idx=99, stats=stats)
    # 15 candle fires (stub ids not in _CHART_PATTERN_IDS) → candle_conf = min(0.80, 15/10) = 0.80
    assert score.confidence == pytest.approx(0.80)


def test_notes_summarises_fire_count(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.scoring import layer2_patterns
    fire = PatternFire(
        pattern_id="hammer", direction="LONG",
        strength=0.5, confidence=0.5, evidence={},
    )
    monkeypatch.setattr(
        layer2_patterns, "ALL_PATTERNS", [_StubPattern("hammer", fire=fire)],
    )
    stats = layer2_patterns.PatternStatsLookup(by_pattern={"hammer": 0.5})
    score = layer2_patterns.score(_bars(), current_idx=99, stats=stats)
    assert "hammer" in score.notes
    # spec §12 Q4: note kept short
    assert len(score.notes) <= 500


# ---- load_pattern_stats (E2 unit-side) -----------------------------------


class _StubResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _StubSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.last_params: dict[str, Any] | None = None

    async def execute(
        self, _stmt: Any, params: dict[str, Any] | None = None,
    ) -> _StubResult:
        self.last_params = params
        return _StubResult(self._rows)


@dataclass
class _Row:
    pattern_id: str
    n_samples: int
    n_correct: int


@pytest.mark.asyncio
async def test_load_pattern_stats_warm_rows_become_accuracies() -> None:
    """n_samples >= 50 → ratio; below threshold → omitted (prior applies)."""
    from app.core.scoring.layer2_patterns import (
        COLD_START_THRESHOLD,
        PRIOR_ACCURACY,
        load_pattern_stats,
    )

    rows = [
        _Row(pattern_id="hammer", n_samples=100, n_correct=60),
        _Row(pattern_id="doji", n_samples=COLD_START_THRESHOLD - 1, n_correct=30),
        _Row(pattern_id="engulfing", n_samples=200, n_correct=110),
    ]
    session = _StubSession(rows)
    lookup = await load_pattern_stats(session, symbol="BTC/USDT", timeframe="1h")  # type: ignore[arg-type]

    assert lookup.get("hammer") == pytest.approx(0.60)
    assert lookup.get("engulfing") == pytest.approx(0.55)
    # Cold-start row excluded → prior used.
    assert lookup.get("doji") == pytest.approx(PRIOR_ACCURACY)
    # Bind params include both filters.
    assert session.last_params == {"sym": "BTC/USDT", "tf": "1h"}


@pytest.mark.asyncio
async def test_load_pattern_stats_no_rows_returns_empty_lookup() -> None:
    from app.core.scoring.layer2_patterns import (
        PRIOR_ACCURACY,
        load_pattern_stats,
    )
    session = _StubSession([])
    lookup = await load_pattern_stats(session, symbol="ETH/USDT", timeframe="4h")  # type: ignore[arg-type]
    assert lookup.by_pattern == {}
    assert lookup.get("anything") == pytest.approx(PRIOR_ACCURACY)
