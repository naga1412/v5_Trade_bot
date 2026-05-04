# SP-2 — Indicators + Patterns Library Design Spec

**Date:** 2026-05-05
**Status:** Approved (autonomous-mode default; user can redirect)
**Implementation target:** Sub-project SP-2 (after SP-1 infra ship; parallel-friendly)
**Depends on:** SP-0 (3 layers + RSI/MACD/EMA exist), SP-1 (`pattern_stats` table + nightly job already wired)
**Companion specs:** `2026-05-01-trading-radar-meta-plan-design.md` §2.4

---

## 1. Purpose

Add the **pattern detection layer (L2)** + complete the **indicator library** to trading-radar. The bot currently uses a small subset (RSI, MACD, EMA20/50, ATR) hand-wired into 3 scoring layers (L1, L3, L5). This spec ships:

- **43 indicators** total (12 already exist; 31 new)
- **158 patterns** total: **82 candle patterns** (via TA-Lib) + **76 chart patterns** (custom)
- **L2 layer scoring aggregator** that applies the meta-plan §2.4 voting scheme
- **Cross-validation harness** that compares each indicator/pattern's output against TradingView Pine Script reference values on 100 sample bars

After SP-2, all 10 scoring layers' inputs exist (L4/L6/L7/L8/L9/L10 layers themselves come in later sub-projects, but their input features are all in place).

### Non-goals

- **No new scoring layers.** L2 is the only layer this spec WIRES; L4, L6, L7, L8, L9 use SP-2's outputs but are implemented in their own sub-projects.
- **No backtesting framework.** Belongs to SP-5.
- **No UI surface for individual indicators/patterns.** Indicators feed the scoring panels (already exist); patterns feed L2 scores (one slot in the existing layer breakdown). New per-pattern viz is SP-6.
- **No retroactive pattern_stats backfill.** SP-1 wired the nightly job; pattern fires from SP-2 onward populate `pattern_stats`. Historical bars are NOT scanned to seed counts (would require separate worker; deferred to SP-1.5 or SP-5).

---

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Candle pattern engine | **TA-Lib** (`talib` Python wrapper over the C library) — battle-tested, fast, no reinvention |
| 2 | Chart pattern engine | **Custom Python** — no good open-source coverage of the 76-pattern set; builds on numpy/pandas |
| 3 | Pattern interface | `detect(bars: pd.DataFrame, current_idx: int) -> PatternFire | None` — pure function, no DB |
| 4 | PatternFire dataclass | `{pattern_id: str, direction: Literal["LONG","SHORT"], strength: float ∈ [0,1], confidence: float ∈ [0,1], evidence: dict[str, Any]}` |
| 5 | L2 scoring formula (per meta-plan §2.4) | `Σ_patterns (strength × confidence × historical_accuracy)` then squashed to `[-1, 1]` via tanh |
| 6 | Historical accuracy lookup | `pattern_stats` table (created in SP-1 migration 0007); prior `0.5` when `n_samples < 50` |
| 7 | Per-asset / per-TF stats | Yes — `pattern_stats` keyed by `(pattern_id, symbol, timeframe)` |
| 8 | Cross-validation tolerance | **≤ 0.1% absolute difference** vs TradingView reference values on 100 sample bars (per meta-plan §3 §176) |
| 9 | Sample selection for cross-check | 10 random BTC/USDT 1h bars × 10 patterns/indicators = 100 (mix of pattern and indicator validations) |
| 10 | TA-Lib install path | Pre-built `ta-lib==0.6.0` wheel (PyPI provides ARM64 + x86_64 wheels since 2024); fallback to apt `python3-talib` on Linux ARM if wheel unavailable |
| 11 | Run cadence | Synchronous — patterns + indicators evaluated on each closed candle inside `build_prediction()`; no background job needed |
| 12 | Per-asset enable/disable | Yes — `pattern_enabled` table (id, pattern_id, symbol, timeframe, enabled) lets admin disable noisy patterns; defaults to enabled for all |
| 13 | Acceptance bar | All 158 patterns have a `detect()` function with at least one passing test; cross-validation samples (100) all within 0.1% |

---

## 3. Architecture

### 3.1 Module layout

```
backend/app/core/
├── indicators/                 (existing — extend)
│   ├── __init__.py             — registry of all 43 indicators
│   ├── ema.py                  ✓ exists
│   ├── rsi.py                  ✓ exists
│   ├── macd.py                 ✓ exists
│   ├── atr.py                  NEW
│   ├── bollinger.py            NEW
│   ├── stochastic.py           NEW
│   ├── adx.py                  NEW
│   ├── ichimoku.py             NEW
│   ├── williams_r.py           NEW
│   ├── obv.py                  NEW
│   ├── mfi.py                  NEW
│   ├── cci.py                  NEW
│   ├── roc.py                  NEW
│   ├── donchian.py             NEW
│   ├── keltner.py              NEW
│   ├── psar.py                 NEW
│   ├── sma.py                  NEW
│   ├── vwap.py                 NEW
│   ├── tsi.py                  NEW
│   ├── ultimate.py             NEW
│   ├── trix.py                 NEW
│   ├── vortex.py               NEW
│   ├── chaikin_money_flow.py   NEW
│   ├── force_index.py          NEW
│   ├── ease_of_movement.py     NEW
│   └── ... (43 total)
├── patterns/                   NEW
│   ├── __init__.py             — registry of all 158 patterns
│   ├── base.py                 — PatternFire dataclass + Pattern protocol
│   ├── candle/                 — 82 candle patterns (TA-Lib wrappers)
│   │   ├── __init__.py         — talib registry; one-line wrappers per pattern
│   │   ├── doji.py
│   │   ├── hammer.py
│   │   ├── engulfing.py
│   │   └── ... (82 modules, mostly 5-15 lines each)
│   └── chart/                  — 76 chart patterns (custom)
│       ├── __init__.py
│       ├── double_top.py
│       ├── head_shoulders.py
│       ├── triangle.py
│       └── ... (76 modules)
└── scoring/
    ├── layer2_patterns.py      NEW — aggregates all 158 pattern fires per bar into L2 score
    └── ... (existing layers)
```

### 3.2 Pattern interface

```python
from dataclasses import dataclass
from typing import Any, Literal, Protocol
import pandas as pd

@dataclass(frozen=True)
class PatternFire:
    pattern_id: str
    direction: Literal["LONG", "SHORT"]
    strength: float           # [0, 1] — how strongly the pattern is formed
    confidence: float         # [0, 1] — how clean / unambiguous it is
    evidence: dict[str, Any]  # e.g. {"hammer_ratio": 2.3, "lookback": 20}

class Pattern(Protocol):
    pattern_id: str
    pattern_type: Literal["candle", "chart"]

    def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
        """Run detection on `bars` ending at `current_idx`. Returns None if not detected."""
        ...
```

A canonical candle pattern wrapping TA-Lib:

```python
# patterns/candle/hammer.py
import talib

class HammerPattern:
    pattern_id = "hammer"
    pattern_type = "candle"

    def detect(self, bars, current_idx):
        result = talib.CDLHAMMER(
            bars["open"].values, bars["high"].values,
            bars["low"].values, bars["close"].values,
        )
        val = result[current_idx]
        if val == 0:
            return None
        direction = "LONG" if val > 0 else "SHORT"
        # TA-Lib output: -100 to +100; normalize strength = abs/100
        strength = min(1.0, abs(val) / 100.0)
        confidence = 0.7  # candle patterns from TA-Lib are well-defined
        return PatternFire(
            pattern_id=self.pattern_id, direction=direction,
            strength=strength, confidence=confidence,
            evidence={"talib_output": int(val)},
        )
```

A custom chart pattern:

```python
# patterns/chart/double_top.py
import numpy as np

class DoubleTopPattern:
    pattern_id = "double_top"
    pattern_type = "chart"
    LOOKBACK = 60

    def detect(self, bars, current_idx):
        if current_idx < self.LOOKBACK:
            return None
        window = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = window["high"].values
        # Find the two highest peaks
        peak_indices = self._find_peaks(highs, prominence=0.5)
        if len(peak_indices) < 2:
            return None
        # Top two peaks within 1% of each other?
        top_two = sorted(peak_indices, key=lambda i: -highs[i])[:2]
        h1, h2 = highs[top_two[0]], highs[top_two[1]]
        if abs(h1 - h2) / max(h1, h2) > 0.01:
            return None
        # Did price drop below the trough between them?
        trough = min(highs[min(top_two):max(top_two)+1])
        last_close = window["close"].iloc[-1]
        if last_close < trough:
            strength = (max(h1, h2) - last_close) / max(h1, h2)
            return PatternFire(
                pattern_id=self.pattern_id, direction="SHORT",
                strength=min(1.0, strength * 5),  # amplify since drops < 20% are still meaningful
                confidence=0.6,
                evidence={"peak1_high": float(h1), "peak2_high": float(h2),
                          "trough": float(trough), "lookback": self.LOOKBACK},
            )
        return None

    def _find_peaks(self, arr, prominence): ...
```

### 3.3 L2 scoring aggregator

```python
# core/scoring/layer2_patterns.py
import math
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.patterns import ALL_PATTERNS  # list[Pattern]
from app.core.patterns.base import PatternFire
from app.core.scoring.types import LayerScore, Direction

PRIOR_ACCURACY = 0.5

@dataclass(frozen=True)
class PatternStatsLookup:
    """Bulk-loaded accuracies for one (symbol, timeframe). Avoids 158 DB queries per bar."""
    by_pattern: dict[str, float]  # pattern_id -> accuracy

    def get(self, pattern_id: str) -> float:
        return self.by_pattern.get(pattern_id, PRIOR_ACCURACY)

async def load_pattern_stats(session: AsyncSession, symbol: str, timeframe: str) -> PatternStatsLookup:
    rows = (await session.execute(
        sa.text("SELECT pattern_id, n_samples, n_correct FROM pattern_stats "
                "WHERE symbol = :sym AND timeframe = :tf"),
        {"sym": symbol, "tf": timeframe},
    )).all()
    by_pattern = {}
    for r in rows:
        by_pattern[r.pattern_id] = (
            r.n_correct / r.n_samples if r.n_samples >= 50 else PRIOR_ACCURACY
        )
    return PatternStatsLookup(by_pattern=by_pattern)

def score(
    bars: pd.DataFrame,
    *,
    current_idx: int,
    stats: PatternStatsLookup,
    enabled_patterns: set[str] | None = None,  # None = all enabled
) -> LayerScore:
    """L2 score: weighted vote across all enabled pattern fires at `current_idx`."""
    fires: list[PatternFire] = []
    for pat in ALL_PATTERNS:
        if enabled_patterns is not None and pat.pattern_id not in enabled_patterns:
            continue
        try:
            fire = pat.detect(bars, current_idx)
            if fire is not None:
                fires.append(fire)
        except Exception:  # pragma: no cover — never let a pattern crash the whole layer
            continue

    long_score = sum(
        f.strength * f.confidence * stats.get(f.pattern_id)
        for f in fires if f.direction == "LONG"
    )
    short_score = sum(
        f.strength * f.confidence * stats.get(f.pattern_id)
        for f in fires if f.direction == "SHORT"
    )
    raw = long_score - short_score
    # Squash via tanh — typical raw values [-5, +5] → [-1, +1]
    squashed = math.tanh(raw / 3.0)

    if abs(squashed) < 0.05:
        direction = Direction.NEUTRAL
    elif squashed > 0:
        direction = Direction.LONG
    else:
        direction = Direction.SHORT

    return LayerScore(
        direction=direction,
        strength=abs(squashed),
        confidence=min(1.0, len(fires) / 10.0),  # more fires = more confident
        notes=f"{len(fires)} patterns fired",
    )
```

### 3.4 Integration with `predictor.py`

Existing `app/core/predictor.py:build_prediction()` runs L1, L3, L5 layers. Extend to also run L2:

```python
layer_results[2] = await score_l2(
    bars,
    current_idx=len(bars) - 1,
    stats=await load_pattern_stats(session, symbol, timeframe),
)
```

This requires the predictor to take an `AsyncSession` (currently doesn't). Refactor: thread the session through, OR cache `PatternStatsLookup` in process memory and refresh nightly (cheaper, since stats only update nightly).

**Decision: process-memory cache** — the worker loads stats once per (symbol, timeframe) at startup + after the nightly pattern_stats job runs. Avoids per-bar DB hits.

---

## 4. Data model

### 4.1 `pattern_stats` table

Already created in SP-1 migration 0007. Schema unchanged. SP-2 just starts populating it via the nightly job (also already wired in SP-1).

### 4.2 `pattern_enabled` table (NEW)

```sql
CREATE TABLE pattern_enabled (
    id BIGSERIAL PRIMARY KEY,
    pattern_id TEXT NOT NULL,
    symbol TEXT NOT NULL,                -- 'BTC/USDT' or '*' for global
    timeframe TEXT NOT NULL,             -- '1h' or '*' for any
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    disabled_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT REFERENCES users(id),
    UNIQUE (pattern_id, symbol, timeframe)
);
CREATE INDEX pattern_enabled_lookup_idx ON pattern_enabled (symbol, timeframe, enabled);
```

Default behavior: row absent ⇒ enabled. Admin disables a noisy pattern by inserting a row with `enabled=false`.

Migration 0009 creates this table.

### 4.3 No changes to `predictions`

The L2 score lands in `predictions.layer_scores` JSONB under key `"2"` — same pattern as existing L1/L3/L5.

---

## 5. Cross-validation harness

### 5.1 Reference data

The user picks 10 random BTC/USDT 1h bars from 2025 (post-test-set period — independent from training). For each bar, captures the TradingView output of:
- 5 representative indicators (RSI, MACD, ATR, Bollinger, Stochastic)
- 5 representative patterns (Hammer, Engulfing, Doji, Double Top, Head & Shoulders)

Total: 100 reference values stored as JSON in `tools/validation/sp2_reference.json`.

### 5.2 Validation script

```python
# tools/validation/sp2_cross_check.py
def main() -> None:
    reference = json.load(open("tools/validation/sp2_reference.json"))
    failures = []
    for sample in reference:
        ours = compute_indicator_or_pattern(sample["component"], sample["bars"])
        their_value = sample["tradingview_value"]
        diff_pct = abs(ours - their_value) / max(abs(their_value), 1e-6)
        if diff_pct > 0.001:  # 0.1% acceptance
            failures.append((sample["component"], sample["bar_ts"], ours, their_value, diff_pct))
    if failures:
        print(f"FAIL: {len(failures)} samples exceed 0.1% threshold")
        for f in failures: print(f)
        sys.exit(1)
    print(f"PASS: all 100 samples within 0.1%")
```

### 5.3 What "passes" means

- Indicators: numeric diff ≤ 0.1% (matches existing SP-0 cross-check on RSI/EMA/MACD)
- Candle patterns: TA-Lib output value matches (deterministic; no diff)
- Chart patterns: detection boolean matches (custom code matches our own reference); strength/confidence are subjective so they're spot-checked manually, not auto-validated

---

## 6. Validation procedure

1. After SP-2 ships, run `python tools/validation/sp2_cross_check.py` → exits 0 if 100/100 pass
2. Inspect `pattern_stats` table after 7 days of live data — should have rows for ~30+ patterns × 30 assets × 1 TF (most patterns will fire infrequently)
3. Spot-check Tab 1 with a known historical pattern (e.g., load BTC/USDT 1h history at a known double-top date, confirm L2 score reflects bearish bias)

---

## 7. Sub-project sequencing

This spec is implemented as **SP-2**, after SP-1 (infra) and parallel-friendly with SP-3 (data adapters). The 158-pattern set is naturally parallelizable:
- **Subagent 1:** indicators (43)
- **Subagent 2:** candle patterns (82) — mostly TA-Lib wrappers, fast
- **Subagent 3:** chart patterns (76) — custom code, slowest

After SP-2 ships, the natural next sub-projects:
- **SP-3** — Data adapters + universe (parallel-safe; can start simultaneously with SP-2)
- **SP-5** — Full scoring + traps (depends on SP-2 + SP-3)
- **SP-1.1** — Train first Conv-LSTM checkpoint (orthogonal; can happen anytime)

---

## 8. Implementation cost estimate

- Sub-project size: **~40-60 tasks across 5 phases** (one phase per parallel subagent group + integration + validation + ship)
- Wall-clock: **~3-4 weeks of subagent-driven work** (per meta-plan §3 §176, with 3-subagent parallelism shaving wall-clock)
- Phase ordering:
  - **Phase A — Worktree + migration 0009 (`pattern_enabled`) + Pattern protocol + L2 scoring scaffold** (~5 tasks)
  - **Phase B — Indicators (43 modules)** — parallel-safe
  - **Phase C — Candle patterns (82 modules, mostly TA-Lib wrappers)** — parallel with B
  - **Phase D — Chart patterns (76 modules, custom)** — parallel with B/C
  - **Phase E — Integration: predictor.py wires L2; in-memory stats cache; per-asset enable/disable admin endpoint** (~5 tasks)
  - **Phase F — Cross-validation harness + 100-sample reference + ship** (~5 tasks)
- New backend modules: `app/core/patterns/`, `app/core/scoring/layer2_patterns.py`, `app/api/routes/admin_patterns.py` (enable/disable endpoint), 31 new indicator modules
- New frontend: minimal — pattern enable/disable toggle in Admin → Patterns sub-page (Phase F or deferred to SP-6)
- Database migrations: 1 (0009 adds `pattern_enabled` table)
- Test coverage: every pattern has at least one test verifying it fires on a hand-crafted bar pattern + doesn't fire on a non-matching one

---

## 9. Cross-cutting policy compliance

| Policy | How SP-2 satisfies it |
|---|---|
| §5.14 audit hash chain | L2 score lands in `predictions.layer_scores` (already chained via `insert_with_chain`); no new chain |
| §2.6 Cloudflare Access | Admin pattern-enable endpoint inherits `Depends(require_admin)` |
| Per-user (SP-0.7) | Pattern detections are global (deterministic from market data); user_id stays on `predictions` table; `pattern_enabled` is admin-scoped (no user_id) |
| TA-Lib install | Dockerfile's `pip install ta-lib==0.6.0` should resolve to a pre-built wheel for ARM64 + x86_64. If wheel missing on build, fall back to `apt install python3-talib` per meta-plan §2.7 |

---

## 10. Risk + fallback plan

| Failure mode | Detection | Fallback |
|---|---|---|
| TA-Lib wheel unavailable for backend image's Python+arch | Docker build fails | apt-install fallback OR build from source (~30 min) |
| Specific TA-Lib pattern returns garbage on edge cases | Cross-validation script catches it | Wrap with `try/except`, log + skip that pattern; flag for re-implementation |
| Chart pattern false-positive rate too high (e.g., double_top fires every 3 bars) | nightly pattern_stats shows accuracy < 0.3 over 100+ samples | Admin disables via `pattern_enabled` row; investigate code |
| L2 scoring becomes too noisy (final_score swings wildly) | Manual chart inspection + L2 panel value | Tune the `tanh(raw / X)` divisor; raise from 3.0 to 5.0 to reduce sensitivity |
| pattern_stats table growth (rows × symbols × TFs × patterns) | Row count grows ~158 × 30 × 1 = ~5K max → tiny, no issue |
| Pattern detection slows down `build_prediction()` | Per-bar timing logs | Cache `_find_peaks` outputs, batch chart-pattern runs, fall back to subset of most-accurate patterns |

**Crucially: SP-2 failure does NOT brick the bot.** L2 score = 0 (NEUTRAL) when patterns module is broken; existing L1+L3+L5 + (eventually) L8 ML score continue. The full scoring engine is degraded but functional.

---

## 11. Acceptance criteria

- [ ] All 43 indicators implemented with at least 1 test each
- [ ] All 82 candle patterns wrapped via TA-Lib with at least 1 test each
- [ ] All 76 chart patterns implemented with at least 1 test each
- [ ] `tools/validation/sp2_cross_check.py` exits 0 (100/100 samples within 0.1% tolerance)
- [ ] L2 score lands in `predictions.layer_scores["2"]` on every closed candle (BTC live + multi-asset shadow)
- [ ] Pattern-stats nightly job (already wired in SP-1) inserts rows after 24 hours of live data
- [ ] Admin can disable a pattern via `POST /api/v1/admin/patterns/{pattern_id}/disable` (or similar)
- [ ] No regression in existing 360+ backend tests
- [ ] TA-Lib successfully installs in the docker image on rebuild

---

## 12. Open questions (to be resolved during implementation)

| # | Question | Resolved during |
|---|---|---|
| 1 | Should the 158 pattern-name list be exhaustively enumerated in this spec, or pulled from MASTER_PLAN.md? | Phase A — implementer pulls from MASTER_PLAN's pattern catalog OR uses TA-Lib's `talib.get_function_groups()['Pattern Recognition']` (returns 61 — the spec's 82 is an over-count; reconcile during Phase C) |
| 2 | Confidence values for TA-Lib patterns — are they all 0.7, or per-pattern? | Phase C — start with 0.7 default; tune via `pattern_stats` accuracy as data arrives |
| 3 | Should chart patterns share helper functions (e.g., `find_swing_highs`)? | Phase D — extract `app/core/patterns/chart/_helpers.py` for shared swing detection |
| 4 | When a pattern fires, should the `evidence` dict be persisted to `predictions`? | Phase E — yes, inside `layer_scores["2"].notes` as a compact JSON string (limited to 500 chars to avoid bloat) |
| 5 | Should `pattern_enabled` admin endpoint be REST + UI, or just SQL for v1? | Phase F — REST endpoint always; UI optional, defer to SP-6 if time-pressed |

---

## 13. Reference

- Meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §2.4, §3 §176
- SP-1 spec (`pattern_stats` schema): `docs/superpowers/specs/2026-05-05-SP-1-ml-data-ghost-candles-design.md` §4.3
- TA-Lib reference: https://ta-lib.org/function.html (Pattern Recognition section)
- Pattern catalog (158 names): defer to MASTER_PLAN.md or per-source tracking spreadsheet during Phase A enumeration

---

**END OF SP-2 INDICATORS + PATTERNS DESIGN SPEC**
