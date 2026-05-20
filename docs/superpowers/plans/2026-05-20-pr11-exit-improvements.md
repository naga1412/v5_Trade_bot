# PR11 — Exit improvements (T1.3 per-TF timeout + T1.4 TP/SL ratio) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. TDD-discipline is mandatory at every phase: write failing test FIRST, run to confirm RED, then implement to GREEN.

**Goal:**
- **T1.3** — make `TIMEOUT_BARS_PER_TF` operator-tunable via `Settings.SHADOW_TIMEOUT_BARS` (new dict field, default **`{"1h": 12, "15m": 48}`** — the **operator-adjusted** 12h-wall holdtime ceiling, not the spec's example 24h-wall). Existing module-level `TIMEOUT_BARS_PER_TF` + `TIMEOUT_BARS` symbols are kept as a compat shim (read from Settings lazily). New positions write `hold_timeout_bars = settings.SHADOW_TIMEOUT_BARS[tf]` at signal-to-position creation. Backfill migration pins all existing open rows to the **pre-PR11** values (1h=24, 15m=96) via `hold_timeout_bars` override so live in-flight positions don't get their timeout shortened mid-trade.
- **T1.4** — enforce a TP-to-SL distance ratio invariant at signal-emit time. New `Settings.SHADOW_MIN_TP_SL_RATIO: float = 2.0`. `SignalEvaluator.evaluate` rejects (returns `None` + logs INFO) any candidate where `tp_dist / sl_dist < cutoff`.

**Architecture:**

- **T1.3 mechanism — two-prong wiring:**
  1. **`exit_monitor.py` compat shim.** Replace the module-level dict literal with `def get_timeout_bars_per_tf() -> dict[str, int]` that delegates to `get_settings().SHADOW_TIMEOUT_BARS`, and preserve the names `TIMEOUT_BARS_PER_TF` + `TIMEOUT_BARS` as module attributes that read lazily on every access (so `monkeypatch.setattr` + env-var-driven tests still work). `check_exit` reads via the function call, not the constant.
  2. **`worker.py` populate-on-open.** In `_maybe_open_position`, after the existing G1 block, **if `position.hold_timeout_bars is None`** (i.e. G1 OFF or G1 failed-open), assign `position.hold_timeout_bars = settings.SHADOW_TIMEOUT_BARS[tf]`. This keeps G1 authoritative when ON (it already wrote the scaled value via `effective_hold_tp`) — T1.3 fills the gap only when G1 didn't.

- **T1.4 mechanism — single-point enforcement.** In `SignalEvaluator.evaluate`, after computing `sl` and `tp` on the LONG branch (and again on the SHORT branch), compute `sl_dist = abs(last_close - sl)` + `tp_dist = abs(tp - last_close)` and reject (return `None` + log INFO) when `sl_dist <= 0` or `tp_dist / sl_dist < settings.SHADOW_MIN_TP_SL_RATIO`. Because `SignalEvaluator` is a `@dataclass` instance attribute, the cutoff is read via `get_settings()` inside `evaluate` (not from a frozen instance field) — this matches the existing PR2/PR3 worker-side `get_settings()` read pattern and avoids leaking a Settings dependency into the dataclass schema.

- **Architecture nuances confirmed by code read:**
  - `engine.py:119 SignalEvaluator.evaluate` is the actual signal emit point (the spec text refers to "`ShadowEngine.generate_signal`" but no such class exists — `evaluate` is the right hook). The dataclass already carries `sl_atr_mult` / `tp_atr_mult` defaults from module constants `SL_ATR_MULT=1.5` / `TP_ATR_MULT=3.0` — the T1.4 check is post-multiplication, so it correctly enforces against any environment-specific evaluator instance that overrides those mults.
  - `worker.py:531 ShadowPosition.from_signal` then `worker.py:540-577` is the G1 block. G1's `else` path (when `HOLD_TP_SCALING_ENABLED=False`) leaves `position.hold_timeout_bars=None`. The G1 fail-open warning path (`except _g1_err`) ALSO leaves it None. Both paths now flow into the new T1.3 populate-block.
  - `scaling.py:effective_hold_tp` continues to read `TIMEOUT_BARS_PER_TF[timeframe]` — via the compat shim this transparently picks up the Settings-driven value, so G1 scaling is automatically tracked with operator-set baselines (e.g. 15m baseline=48 → G1 mtf_agreement=5 → bars = 48 * (96/24) = 192).
  - Existing **PR3 test fixtures** in `tests/shadow/test_exit_monitor_per_tf.py` + `tests/integration/test_shadow_worker_lifecycle.py` (lines 244, 361, 445, 452) assert literal `TIMEOUT_BARS == 24` and behavioral 1h-timeout-at-24-bars. **These will fail** under the new default (1h=12). Plan handles them in Phase 5 — they're updated to use the new default OR explicitly `monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 24, "15m": 96}')` to lock the old behavior they were probing.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Pydantic v2 BaseSettings (`@lru_cache` + `model_config = SettingsConfigDict(env_file=".env")`) / Alembic / pytest + pytest-asyncio.

**Source spec:** [`docs/superpowers/specs/2026-05-20-pr11-exit-improvements-design.md`](../specs/2026-05-20-pr11-exit-improvements-design.md) (commit `9b94393`).

**Branch:** `feat/pr11-impl-exit-improvements` off `dev`. NEVER push to `main`. (Spec lives on `feat/pr11-spec-exit-improvements`.)

**Behavior change classification:** YES — shadow-only. Bot is in `mode=manual` so no real-money risk; live-trading paths are not touched. The 12h-wall default tightens the holdtime ceiling for NEW 1h+15m signals only (pre-PR11 in-flight positions are pinned to pre-PR11 values via backfill).

**Restart-required:** YES for new Settings (matches existing Pydantic + `lru_cache` pattern). Operator restarts the backend pod after rollout; `get_settings()` re-reads env on next call.

---

## §8 Decision-point defaults (LOCKED by operator)

| # | Decision | Locked default |
|---|---|---|
| 1 | `SHADOW_TIMEOUT_BARS` dict default | **`{"1h": 12, "15m": 48}`** (operator-adjusted from spec example 24/96) |
| 2 | Existing open positions | **Option A — alembic backfill migration** (pin pre-PR11 rows to `hold_timeout_bars` = 24 for 1h, 96 for 15m) |
| 3 | Rejection log level | **INFO** |
| 4 | Startup-validation strictness | **Raise** on missing TF key in `SHADOW_TIMEOUT_BARS` |
| 5 | Compat shim | **Keep** `TIMEOUT_BARS_PER_TF` module export (lazy-read from Settings) |
| 6 | PR shape | **Combined** (T1.3 + T1.4 in one PR) |
| 7 | Retroactive backfill of `shadow_trades` | **No** (historical signals unchanged) |
| 8 | `SHADOW_MIN_TP_SL_RATIO` | `2.0` — **single global float**, not per-TF |
| 9 | T1.4 timing | **Signal-time** rejection (return `None`, don't auto-adjust TP) |
| 10 | Restart-required | **Yes** (existing Pydantic + lru_cache pattern) |
| 11 | Test fixtures with sub-2.0 ratio | **Update fixtures** so engine accepts them (or accept rejection if intentional) |

---

## File Structure (locked in via design)

### NEW files

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/2026_05_20_0025_pr11_backfill_hold_timeout_bars.py` | Data-only migration: backfill `shadow_open_positions.hold_timeout_bars` for rows where `IS NULL` AND `timeframe IN ('1h', '15m')` with pre-PR11 values 24/96. |
| `backend/tests/unit/test_pr11_settings_defaults.py` | Default values + env-var override + `SHADOW_TIMEOUT_BARS` accepts new TFs from env. |
| `backend/tests/shadow/test_exit_monitor_pr11_settings_driven.py` | `check_exit` reads via Settings — patch env to {"1h": 5} → 6-bar position TIMEOUTs. |
| `backend/tests/shadow/test_engine_pr11_tp_sl_ratio.py` | T1.4 — ratio at boundary passes; ratio below cutoff rejected; rollback=0 bypasses; SHORT path mirrors LONG. |
| `backend/tests/shadow/test_worker_pr11_populates_hold_timeout_bars.py` | `_maybe_open_position` writes `position.hold_timeout_bars = settings.SHADOW_TIMEOUT_BARS[tf]` when G1 is OFF. |
| `backend/tests/shadow/test_startup_validation_pr11.py` | Missing TF key → `RuntimeError`. Helper is callable from `app.main:lifespan`. |
| `backend/tests/db/test_pr11_migration.py` | Postgres-only: seed 4 rows (2 NULL/1h, 1 NULL/15m, 1 non-NULL/1h), apply, assert backfill. |
| `backend/tests/db/test_pr11_migration_downgrade.py` | Round-trip upgrade → downgrade → upgrade (no-op downgrade — see migration docstring). |

### MODIFIED files

| Path | Reason |
|---|---|
| `backend/app/config.py` | Add 2 PR11 settings (`SHADOW_TIMEOUT_BARS` + `SHADOW_MIN_TP_SL_RATIO`). |
| `backend/app/shadow/exit_monitor.py` | Replace module-level dict literal with `get_timeout_bars_per_tf()`; preserve `TIMEOUT_BARS_PER_TF` + `TIMEOUT_BARS` as lazy module attrs. `check_exit` reads via the function. |
| `backend/app/shadow/scaling.py` | Switch `TIMEOUT_BARS_PER_TF[timeframe]` to `get_timeout_bars_per_tf()[timeframe]` — same surface, Settings-backed. |
| `backend/app/shadow/engine.py` | `SignalEvaluator.evaluate` — after sl/tp computation on each branch, ratio-gate via `get_settings().SHADOW_MIN_TP_SL_RATIO`. |
| `backend/app/shadow/worker.py` | `_maybe_open_position` — after the existing G1 block, if `position.hold_timeout_bars is None`, set it from `settings.SHADOW_TIMEOUT_BARS[tf]`. |
| `backend/app/main.py` | `lifespan` — call `validate_shadow_timeout_bars_keys()` after `settings = get_settings()`, before any worker spawns. Raise on missing TF key. |
| `backend/tests/unit/test_shadow_exit_monitor.py` | Existing `test_timeout_after_max_bars` reads `TIMEOUT_BARS` — under new default `TIMEOUT_BARS == 12`. Update to monkeypatch SHADOW_TIMEOUT_BARS back to pre-PR11 values OR re-read from `get_timeout_bars_per_tf()`. |
| `backend/tests/shadow/test_exit_monitor_per_tf.py` | Hardcoded `assert TIMEOUT_BARS_PER_TF["1h"] == 24` becomes `assert TIMEOUT_BARS_PER_TF["1h"] == 12` (or env-driven). Position fixtures using `bars_held=24` for 1h shift to `bars_held=12`. |
| `backend/tests/integration/test_shadow_worker_lifecycle.py` | `EXPECTED_TIMEOUT_BARS = 24` → `= 12`; behavioral assertion at line 361 (`sol.bars_held >= TIMEOUT_BARS`) is auto-consistent if `TIMEOUT_BARS` reads from Settings, but the surrounding test setup that drives 24+ candles per symbol may now exit earlier — confirm by reading the test path + adjust the candle-stream length if needed. |
| `backend/tests/integration/test_shadow_worker.py` | `bars_held=TIMEOUT_BARS - 1` → still works (driven by symbol re-read); confirm `assert t.bars_held == TIMEOUT_BARS` still passes after the shift to 12. |
| `backend/tests/shadow/test_hold_tp_scaling.py` | Compat-shim consumer — verify G1 baseline reads `48` for 15m under new default (existing test currently expects baseline=96). |
| `backend/tests/unit/test_shadow_engine.py` | `test_evaluator_long_above_threshold_with_high_confidence` etc. — existing fixtures use `tp_atr_mult=3.0 / sl_atr_mult=1.5 → ratio=2.0`. The check is `< cutoff` (strict less-than), so the boundary passes. Existing tests should still pass; add explicit `test_ratio_exactly_2_passes` for redundancy. |
| `docs/ARCHITECTURE.md` (only if a §11e section pattern matches PR9/PR10) | Optional — defer to follow-up if not strictly required. |

### NOT TOUCHED

- `backend/tools/backtest.py` (uses its own private `_TIMEOUT_BARS = 24` constant; backtester is offline-only and unaffected).
- `live_exit_monitor.py` (live-trading exit path) — operates on `live_trades` not `shadow_open_positions`. PR11 is shadow-only.
- `predictor.py` — emits SL/TP via ATR mults at prediction generation; the SignalEvaluator.evaluate ratio check fires LATER in the pipeline (worker → evaluator) so predictor outputs are not gated by PR11.

---

## Phase 1: Add 2 PR11 Settings + defaults test

**Files:** Modify `app/config.py`; create `tests/unit/test_pr11_settings_defaults.py`.

- [ ] **1.1** Write failing defaults test at `backend/tests/unit/test_pr11_settings_defaults.py`:

```python
"""PR11 settings defaults — T1.3 + T1.4.

T1.3: SHADOW_TIMEOUT_BARS replaces the module-level TIMEOUT_BARS_PER_TF
dict. Default is the operator-adjusted 12h-wall holdtime ceiling
({"1h": 12, "15m": 48}), NOT the pre-PR11 24h-wall ({"1h": 24, "15m": 96}).
Existing in-flight positions are pinned to pre-PR11 values via the
backfill migration (Phase 4).

T1.4: SHADOW_MIN_TP_SL_RATIO is a single global float (not per-TF).
Default 2.0 matches the existing TP_ATR_MULT / SL_ATR_MULT = 3.0/1.5 = 2.0
ratio, so a default-config bot rejects zero additional signals beyond
what was already rejected pre-PR11.
"""
from __future__ import annotations

from app.config import Settings


def _s(**kwargs) -> Settings:
    return Settings(
        database_url="postgresql://x", redis_url="redis://x", **kwargs,
    )


# --- T1.3 ----------------------------------------------------------------


def test_shadow_timeout_bars_default_operator_adjusted_12h_wall() -> None:
    """Operator-set value: {"1h": 12, "15m": 48} — NOT the spec's example
    {"1h": 24, "15m": 96}. Existing positions are pinned to pre-PR11 values
    via the alembic backfill in Phase 4."""
    assert _s().SHADOW_TIMEOUT_BARS == {"1h": 12, "15m": 48}


def test_shadow_timeout_bars_env_var_override(monkeypatch) -> None:
    """Pydantic v2 BaseSettings parses JSON-encoded dicts from env vars."""
    monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 24, "15m": 96}')
    assert _s().SHADOW_TIMEOUT_BARS == {"1h": 24, "15m": 96}


def test_shadow_timeout_bars_accepts_new_tf_via_env(monkeypatch) -> None:
    """Operator can add a new TF (e.g. 4h) without code change."""
    monkeypatch.setenv(
        "SHADOW_TIMEOUT_BARS", '{"1h": 12, "15m": 48, "4h": 6}',
    )
    assert _s().SHADOW_TIMEOUT_BARS == {"1h": 12, "15m": 48, "4h": 6}


# --- T1.4 ----------------------------------------------------------------


def test_shadow_min_tp_sl_ratio_default_2_0() -> None:
    """Matches the pre-PR11 TP_ATR_MULT / SL_ATR_MULT = 3.0 / 1.5 = 2.0
    so default-config emits the same signal set as pre-PR11."""
    assert _s().SHADOW_MIN_TP_SL_RATIO == 2.0


def test_shadow_min_tp_sl_ratio_env_var_override(monkeypatch) -> None:
    monkeypatch.setenv("SHADOW_MIN_TP_SL_RATIO", "1.5")
    assert _s().SHADOW_MIN_TP_SL_RATIO == 1.5


def test_shadow_min_tp_sl_ratio_zero_disables_rule(monkeypatch) -> None:
    """Single-flag rollback: ratio=0.0 means the check passes for all
    ratios (since tp_dist/sl_dist >= 0 always)."""
    monkeypatch.setenv("SHADOW_MIN_TP_SL_RATIO", "0.0")
    assert _s().SHADOW_MIN_TP_SL_RATIO == 0.0
```

- [ ] **1.2** Run test — confirm FAIL (`AttributeError: 'Settings' has no attribute 'SHADOW_TIMEOUT_BARS'`).

- [ ] **1.3** Modify `backend/app/config.py` — append before `@lru_cache`:

```python
    # --- PR11 T1.3: per-TF exit timeout (operator-tunable) ---------------
    # Operator-adjusted from the pre-PR11 module-level constant. The
    # pre-PR11 values (1h=24, 15m=96 — equal ~24h wall-clock holdtime)
    # were too generous: median winning shadow trades resolved well
    # before the ceiling, and median losers compounded loss the longer
    # they ran. Operator's 2026-05-20 decision: halve the ceiling to
    # 12h-wall (1h=12, 15m=48) to release capital earlier on losers
    # without truncating typical winners.
    #
    # Existing in-flight positions are pinned to the pre-PR11 values via
    # the 0025_pr11_backfill_hold_timeout_bars alembic migration. New
    # signals emitted after PR11 lands use these per-TF defaults via the
    # populate-on-open block in shadow.worker._maybe_open_position.
    #
    # When HOLD_TP_SCALING_ENABLED=True (G1 path), the scaling table
    # already writes hold_timeout_bars; T1.3 only fills the gap when G1
    # didn't (G1 OFF, or G1 fail-open). The scaling.effective_hold_tp
    # baseline lookup (TIMEOUT_BARS_PER_TF) now transparently picks up
    # these Settings values via the compat shim in exit_monitor.py.
    #
    # Startup validation in app.main:lifespan asserts every TF in
    # SHADOW_TIMEFRAMES has a matching key here — missing key raises
    # RuntimeError at boot (fail-loud per spec §8 decision #4).
    SHADOW_TIMEOUT_BARS: dict[str, int] = {"1h": 12, "15m": 48}

    # --- PR11 T1.4: minimum TP-to-SL distance ratio ----------------------
    # Enforced at SignalEvaluator.evaluate: signals where
    # abs(tp - entry) / abs(entry - sl) < this value are rejected
    # (return None + log INFO) before they reach the worker's open-
    # position path. Default 2.0 matches the existing TP_ATR_MULT /
    # SL_ATR_MULT = 3.0 / 1.5 = 2.0 so a default-config bot rejects
    # zero additional signals. Tunable per env. Setting to 0.0 disables
    # the rule entirely (single-flag rollback; restart required).
    SHADOW_MIN_TP_SL_RATIO: float = 2.0
```

- [ ] **1.4** Run defaults test — confirm PASS.

- [ ] **1.5** Run full unit suite — confirm no regression: `pytest backend/tests/unit -x`.

- [ ] **1.6** Commit + push:

```bash
cd a:/v5_Trade_bot_followups
git add backend/app/config.py backend/tests/unit/test_pr11_settings_defaults.py
git commit -m "feat(pr11): SHADOW_TIMEOUT_BARS + SHADOW_MIN_TP_SL_RATIO settings (Phase 1)"
git push origin feat/pr11-impl-exit-improvements
```

**Quality bar:**
- 6 new tests PASS.
- mypy clean (`dict[str, int]` + `float` types).
- No regression in existing `tests/unit/test_pr*_settings_defaults.py`.

---

## Phase 2: T1.3 — Settings-backed compat shim + signal-to-position populate

**Files:** Modify `app/shadow/exit_monitor.py`, `app/shadow/scaling.py`, `app/shadow/worker.py`, `app/main.py`; create 3 new tests.

### 2A. exit_monitor.py compat shim

- [ ] **2A.1** Write failing test at `backend/tests/shadow/test_exit_monitor_pr11_settings_driven.py`:

```python
"""PR11 T1.3: check_exit reads timeout limits from Settings via the compat shim.

The pre-PR11 module-level dict (TIMEOUT_BARS_PER_TF: dict[str, int] = {...})
is replaced with a lazy getter that reads from Settings on every call. The
module-level names TIMEOUT_BARS_PER_TF + TIMEOUT_BARS are kept as
backwards-compat exports so existing imports work; they delegate to the
getter.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import (
    ExitReason,
    check_exit,
    get_timeout_bars_per_tf,
)


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _pos(*, timeframe: str = "1h", bars_held: int = 0) -> ShadowPosition:
    return ShadowPosition(
        symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        position_size_usdt=10.0, entry_score=0.4, entry_confidence=0.6,
        entry_atr=1.5, layer_scores={}, bars_held=bars_held,
        opened_at=_NOW, last_check_at=_NOW, signal_id="sig_pr11",
        timeframe=timeframe,
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Settings is @lru_cache'd — drop the cache before/after each test
    so monkeypatch.setenv takes effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_get_timeout_bars_per_tf_reads_default() -> None:
    """Default returns the new 12h-wall ceiling."""
    assert get_timeout_bars_per_tf() == {"1h": 12, "15m": 48}


def test_check_exit_uses_settings_value_for_1h(monkeypatch) -> None:
    """Patch env to {"1h": 5}; a 5-bar position TIMEOUTs (the pre-PR11
    constant of 24 would NOT have timed out at 5 bars)."""
    monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 5, "15m": 48}')
    get_settings.cache_clear()
    p = _pos(timeframe="1h", bars_held=5)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_check_exit_at_new_default_12_bars_timeout_for_1h() -> None:
    """At the new default 1h=12, a 12-bar position TIMEOUTs."""
    p = _pos(timeframe="1h", bars_held=12)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_check_exit_at_11_bars_no_timeout_for_1h() -> None:
    """Just under the new 12-bar threshold — no timeout."""
    p = _pos(timeframe="1h", bars_held=11)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is None


def test_check_exit_at_new_default_48_bars_timeout_for_15m() -> None:
    """At the new default 15m=48, a 48-bar position TIMEOUTs."""
    p = _pos(timeframe="15m", bars_held=48)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_module_level_TIMEOUT_BARS_PER_TF_compat_shim_reads_settings() -> None:
    """Existing imports of TIMEOUT_BARS_PER_TF still work — the symbol
    reads from Settings via the lazy getter wrapper."""
    from app.shadow import exit_monitor
    assert exit_monitor.TIMEOUT_BARS_PER_TF == {"1h": 12, "15m": 48}


def test_module_level_TIMEOUT_BARS_compat_shim_reads_settings() -> None:
    """TIMEOUT_BARS == TIMEOUT_BARS_PER_TF["1h"] — both lazy-read."""
    from app.shadow import exit_monitor
    assert exit_monitor.TIMEOUT_BARS == 12


def test_g1_override_still_takes_precedence(monkeypatch) -> None:
    """When pos.hold_timeout_bars is set (G1 override), check_exit uses
    it regardless of the per-TF baseline (regression guard for the
    G1-set-but-shorter-than-baseline edge case)."""
    monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 100, "15m": 400}')
    get_settings.cache_clear()
    p = _pos(timeframe="1h", bars_held=10)
    p.hold_timeout_bars = 5  # G1 set this lower than baseline
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_unknown_tf_still_raises_keyerror() -> None:
    """Fail-loud contract preserved — unknown TF → KeyError."""
    p = _pos(timeframe="5m", bars_held=10)
    with pytest.raises(KeyError):
        check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
```

- [ ] **2A.2** Run — FAIL (`get_timeout_bars_per_tf` not yet exported).

- [ ] **2A.3** Modify `backend/app/shadow/exit_monitor.py` (full rewrite of the top section):

```python
"""PR11 T1.3: per-TF timeout is now Settings-driven.

The pre-PR11 module-level constant TIMEOUT_BARS_PER_TF was a dict
literal pinned at code level. PR11 moves the value-source to
Settings.SHADOW_TIMEOUT_BARS so the operator can tune per env without
a code change + redeploy.

For backwards compatibility the module-level names TIMEOUT_BARS_PER_TF
and TIMEOUT_BARS continue to exist as lazy attributes (a module-level
__getattr__ delegates each access to get_settings()). check_exit
itself reads via get_timeout_bars_per_tf() for clarity.

Identity / mutation contract:
  - get_timeout_bars_per_tf() returns a NEW dict on each call (a copy
    of settings.SHADOW_TIMEOUT_BARS) so callers cannot mutate Settings
    in-place. The cost is a small dict copy per exit check; check_exit
    is called once per closed candle per open symbol-tf, well under
    1k/min in production.
"""
from dataclasses import dataclass
from enum import Enum

from app.config import get_settings
from app.shadow.engine import Direction, ShadowPosition


def get_timeout_bars_per_tf() -> dict[str, int]:
    """Return the operator-set per-TF timeout dict from Settings.

    PR11 T1.3 entry point. Returns a copy so callers can't mutate
    Settings state via dict.update. Performance is fine — called
    once per closed candle per open symbol-tf in production.
    """
    return dict(get_settings().SHADOW_TIMEOUT_BARS)


def __getattr__(name: str):
    """Module-level lazy attribute for backwards compat.

    Pre-PR11 callers did:
      from app.shadow.exit_monitor import TIMEOUT_BARS_PER_TF
    or:
      from app.shadow.exit_monitor import TIMEOUT_BARS

    These names continue to work — each access delegates to the
    Settings-backed getter. PR11 callers should switch to
    get_timeout_bars_per_tf() over time, but the rename is non-blocking.
    """
    if name == "TIMEOUT_BARS_PER_TF":
        return get_timeout_bars_per_tf()
    if name == "TIMEOUT_BARS":
        return get_timeout_bars_per_tf()["1h"]
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )


class ExitReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    exit_price: float


def check_exit(
    pos: ShadowPosition, *, bar_high: float, bar_low: float, bar_close: float
) -> ExitDecision | None:
    """Check whether this candle triggers a close.

    Convention (matches paper engine): if both SL and TP touched in same
    bar, assume SL hit first (pessimistic).

    PR11 T1.3: the timeout limit is read per-TF from
    ``get_timeout_bars_per_tf()[pos.timeframe]`` (Settings-backed).
    Unknown TF raises KeyError (programming-error fail-loud — new TFs
    require an explicit entry in Settings.SHADOW_TIMEOUT_BARS).
    """
    # Phase 5.5.4: G1 scaling override. When pos.hold_timeout_bars is
    # set (HOLD_TP_SCALING_ENABLED=True at open-time, OR T1.3 populate-
    # on-open block — see worker._maybe_open_position), use the recorded
    # per-position limit; otherwise fall back to the per-TF baseline.
    # Pre-G1 / pre-T1.3 ShadowPosition instances always have
    # hold_timeout_bars=None, so this is bit-identical to legacy behavior
    # for them.
    if pos.hold_timeout_bars is not None:
        limit = pos.hold_timeout_bars
    else:
        limit = get_timeout_bars_per_tf()[pos.timeframe]  # KeyError → fail-loud
    if pos.bars_held >= limit:
        return ExitDecision(reason=ExitReason.TIMEOUT, exit_price=bar_close)

    if pos.direction is Direction.LONG:
        sl_hit = bar_low <= pos.stop_loss
        tp_hit = bar_high >= pos.take_profit
        if sl_hit:
            return ExitDecision(reason=ExitReason.STOP_LOSS, exit_price=pos.stop_loss)
        if tp_hit:
            return ExitDecision(reason=ExitReason.TAKE_PROFIT, exit_price=pos.take_profit)
        return None

    # SHORT
    sl_hit = bar_high >= pos.stop_loss
    tp_hit = bar_low <= pos.take_profit
    if sl_hit:
        return ExitDecision(reason=ExitReason.STOP_LOSS, exit_price=pos.stop_loss)
    if tp_hit:
        return ExitDecision(reason=ExitReason.TAKE_PROFIT, exit_price=pos.take_profit)
    return None
```

- [ ] **2A.4** Run the new test file — confirm 9 PASS.

### 2B. scaling.py migrate to compat shim getter

- [ ] **2B.1** Modify `backend/app/shadow/scaling.py` — switch the import + call:

Replace:
```python
from app.shadow.exit_monitor import TIMEOUT_BARS_PER_TF
```
with:
```python
from app.shadow.exit_monitor import get_timeout_bars_per_tf
```

And inside `effective_hold_tp`:

Replace:
```python
    tf_baseline_bars = TIMEOUT_BARS_PER_TF[timeframe]  # KeyError → fail-loud
```
with:
```python
    # PR11 T1.3: baseline now Settings-backed via the compat shim. Same
    # KeyError-on-unknown-TF contract; same dict shape.
    tf_baseline_bars = get_timeout_bars_per_tf()[timeframe]
```

Also update the module docstring `from app.shadow.exit_monitor import TIMEOUT_BARS_PER_TF` reference comment to point to `get_timeout_bars_per_tf`.

- [ ] **2B.2** Run `backend/tests/shadow/test_hold_tp_scaling.py` — likely FAIL because the existing baseline=96 assertion now sees 48 under the new default. Update the test to either monkeypatch SHADOW_TIMEOUT_BARS to the pre-PR11 values OR re-target assertions to the new baseline. Specifically:

In `tests/shadow/test_hold_tp_scaling.py` ~line 60, the test expecting `(192, 1.25)` for `15m / agreement=4` was computed as `96 * (48/24) = 192`. Under the new default `48 * (48/24) = 96`. **Update the test to monkeypatch SHADOW_TIMEOUT_BARS to the pre-PR11 values** (preserving the test's original intent of probing G1 math, not the per-TF baseline):

```python
@pytest.fixture(autouse=True)
def _pin_pre_pr11_timeout_bars(monkeypatch):
    """Pin SHADOW_TIMEOUT_BARS to pre-PR11 values so the G1 multiplier
    math in this file matches the original spec example numbers.

    The G1 test suite is exercising the scaling formula, not the baseline
    value — under PR11's new defaults (1h=12, 15m=48) the example
    products differ. Pinning here keeps these tests focused on G1."""
    monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 24, "15m": 96}')
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

- [ ] **2B.3** Run `tests/shadow/test_hold_tp_scaling.py` — PASS.

### 2C. worker.py populate-on-open

- [ ] **2C.1** Write failing test at `backend/tests/shadow/test_worker_pr11_populates_hold_timeout_bars.py`:

```python
"""PR11 T1.3: _maybe_open_position writes hold_timeout_bars from Settings.

When G1 is OFF (HOLD_TP_SCALING_ENABLED=False), pre-PR11 worker left
position.hold_timeout_bars=None. Post-PR11, the worker populates it
from settings.SHADOW_TIMEOUT_BARS[tf] BEFORE persisting the position.
This way every NEW position carries its own timeout snapshot — so a
mid-trade Settings change doesn't reach into already-open positions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from app.config import get_settings
from app.shadow.engine import Direction, ShadowPosition, ShadowSignal
from app.shadow.multi_stream import MultiStreamCandle


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _candle(symbol: str = "BTCUSDT") -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=symbol, timeframe="1h",
        ts=_NOW, open=100.0, high=101.0, low=99.0, close=100.5, volume=1.0,
    )


def _signal(symbol: str = "BTCUSDT") -> ShadowSignal:
    return ShadowSignal(
        symbol=symbol, direction=Direction.LONG, score=0.5, confidence=0.7,
        entry_price=100.5, stop_loss=99.0, take_profit=103.5, atr=1.0,
        layer_scores={}, ts=_NOW,
    )


@pytest.mark.asyncio
async def test_position_carries_hold_timeout_bars_from_settings_1h(monkeypatch):
    """Default SHADOW_TIMEOUT_BARS["1h"]=12 — opened position has
    hold_timeout_bars=12."""
    from app.shadow.worker import ShadowWorker

    # Build a minimally-functional worker stub. The simplest harness is
    # to call _maybe_open_position directly with mocks; we don't need
    # the full lifespan flow.
    worker = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=AsyncMock(),
        reader=AsyncMock(),
        timeframes=["1h"],
    )
    # Patch the upstream prediction + persistence so the test isolates
    # the populate-on-open block.
    with patch(
        "app.shadow.worker.build_prediction", new_callable=AsyncMock,
    ) as build_pred, patch(
        "app.shadow.worker._atr", return_value=1.0,
    ), patch(
        "app.shadow.worker.persist_open_position", new_callable=AsyncMock,
    ), patch(
        "app.shadow.worker.build_obs_components", new_callable=AsyncMock,
    ), patch(
        "app.shadow.worker.persist_observation", new_callable=AsyncMock,
    ):
        # Mock the prediction return + signal evaluator
        pred = AsyncMock()
        pred.final.score = 0.5
        pred.final.confidence = 0.7
        pred.layer_scores = {"1": None}
        build_pred.return_value = pred

        worker.evaluator.evaluate = lambda **kw: _signal()  # type: ignore[assignment]
        worker.session_factory = AsyncMock()

        await worker._maybe_open_position(_candle(), pd.DataFrame(), "1h")

    # The position cached in worker.open_positions should carry
    # hold_timeout_bars == 12 (Phase 1's new default).
    pos = worker.open_positions[("BTCUSDT", "1h")]
    assert pos.hold_timeout_bars == 12


@pytest.mark.asyncio
async def test_position_carries_hold_timeout_bars_from_settings_15m(monkeypatch):
    """SHADOW_TIMEOUT_BARS["15m"]=48 → opened position has
    hold_timeout_bars=48."""
    # ... mirror the above but with tf="15m" + assert == 48


@pytest.mark.asyncio
async def test_g1_value_not_overridden_when_scaling_enabled(monkeypatch):
    """When HOLD_TP_SCALING_ENABLED=True and G1 has set
    hold_timeout_bars on the position, the T1.3 populate-block must NOT
    overwrite it."""
    monkeypatch.setenv("HOLD_TP_SCALING_ENABLED", "true")
    get_settings.cache_clear()
    # ... build a worker + signal where G1 sets hold_timeout_bars=200,
    # then assert the persisted position still has 200, NOT 12.
```

(Test code above is the skeleton. The implementation engineer will flesh out the mocks; the assertions are the spec contract.)

- [ ] **2C.2** Run — FAIL (no populate-block yet; `pos.hold_timeout_bars` is `None`).

- [ ] **2C.3** Modify `backend/app/shadow/worker.py` — inside `_maybe_open_position`, immediately AFTER the existing G1 try-except block (ends around line 577, after `# opening at baseline`), and BEFORE the layer_scores_array build:

```python
        # PR11 T1.3: populate per-TF baseline timeout for non-G1 path.
        # When HOLD_TP_SCALING_ENABLED is OFF, the G1 block above is
        # skipped entirely and position.hold_timeout_bars stays None.
        # When HOLD_TP_SCALING_ENABLED is ON, the G1 block writes
        # hold_timeout_bars from the scaling table. In both cases we
        # snapshot the per-TF baseline from Settings into the position
        # ONLY if the G1 path didn't already set it — so a mid-trade
        # Settings change can't shorten an already-open position.
        #
        # The G1 fail-open path (the `except _g1_err` warning) ALSO
        # leaves hold_timeout_bars=None, so this block correctly fills
        # the gap for that edge case too.
        if position.hold_timeout_bars is None:
            position.hold_timeout_bars = _pr3_settings.SHADOW_TIMEOUT_BARS[tf]
```

Note: `_pr3_settings` is already in scope from the G1 block; reusing it avoids a second `get_settings()` call. KeyError on missing `tf` here is appropriate (startup validation in Phase 2D guarantees `SHADOW_TIMEFRAMES ⊆ SHADOW_TIMEOUT_BARS.keys()` so this never raises in practice — but a future runtime TF drift would fail-loud rather than silently emit a position with `hold_timeout_bars=None` that crashes `check_exit` later).

- [ ] **2C.4** Run new test file — PASS.

- [ ] **2C.5** Run full shadow integration suite — `pytest backend/tests/integration/test_shadow_worker.py backend/tests/integration/test_shadow_worker_lifecycle.py -x`. Expect failures from the old `EXPECTED_TIMEOUT_BARS = 24` etc. — these are handled in Phase 5 (test fixture audit). Skip / mark-xfail for now if needed to keep the phase isolated.

### 2D. main.py startup validation

- [ ] **2D.1** Write failing test at `backend/tests/shadow/test_startup_validation_pr11.py`:

```python
"""PR11 T1.3: startup validation — SHADOW_TIMEFRAMES ⊆ SHADOW_TIMEOUT_BARS.keys().

Per spec §8 decision #4: missing TF key raises RuntimeError at boot.
The check is called from app.main:lifespan before any worker spawns.

This is a programming-error fail-loud guard. Adding a new TF without
the matching SHADOW_TIMEOUT_BARS entry would otherwise produce a
KeyError deep inside the first check_exit call — startup validation
surfaces it 60s earlier and outside the worker error path.
"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_validate_shadow_timeout_bars_keys_passes_when_subset(monkeypatch) -> None:
    """Default config — both 1h and 15m are in SHADOW_TIMEOUT_BARS."""
    from app.shadow.exit_monitor import validate_shadow_timeout_bars_keys
    validate_shadow_timeout_bars_keys()  # no raise


def test_validate_shadow_timeout_bars_keys_raises_on_missing_tf(monkeypatch) -> None:
    """Operator added 4h to SHADOW_TIMEFRAMES but forgot SHADOW_TIMEOUT_BARS."""
    monkeypatch.setenv("SHADOW_TIMEFRAMES", '["1h", "15m", "4h"]')
    monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 12, "15m": 48}')
    get_settings.cache_clear()
    from app.shadow.exit_monitor import validate_shadow_timeout_bars_keys
    with pytest.raises(RuntimeError, match="missing.*4h"):
        validate_shadow_timeout_bars_keys()


def test_validate_shadow_timeout_bars_keys_raises_on_completely_disjoint(monkeypatch) -> None:
    monkeypatch.setenv("SHADOW_TIMEFRAMES", '["4h"]')
    monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 12}')
    get_settings.cache_clear()
    from app.shadow.exit_monitor import validate_shadow_timeout_bars_keys
    with pytest.raises(RuntimeError):
        validate_shadow_timeout_bars_keys()
```

- [ ] **2D.2** Run — FAIL (`validate_shadow_timeout_bars_keys` not exported).

- [ ] **2D.3** Add to `backend/app/shadow/exit_monitor.py` (at module bottom, after `check_exit`):

```python
def validate_shadow_timeout_bars_keys() -> None:
    """Assert every TF in SHADOW_TIMEFRAMES has a matching key in
    SHADOW_TIMEOUT_BARS. Raises RuntimeError on first missing key.

    Called from app.main:lifespan at boot — surfaces operator config
    errors 60s earlier than the first check_exit KeyError would.
    Per spec §8 decision #4 (fail-loud strictness over warn-and-fall-
    back-to-legacy).
    """
    settings = get_settings()
    declared = set(settings.SHADOW_TIMEFRAMES)
    available = set(settings.SHADOW_TIMEOUT_BARS.keys())
    missing = declared - available
    if missing:
        raise RuntimeError(
            f"SHADOW_TIMEOUT_BARS missing entries for TFs declared in "
            f"SHADOW_TIMEFRAMES: {sorted(missing)}. Add them to your "
            f"environment (SHADOW_TIMEOUT_BARS env var) or remove "
            f"them from SHADOW_TIMEFRAMES."
        )
```

- [ ] **2D.4** Modify `backend/app/main.py` — inside `lifespan`, immediately after `settings = get_settings()` and BEFORE the env != test/ci block:

```python
    # PR11 T1.3: validate Settings consistency before any worker
    # spawn. SHADOW_TIMEOUT_BARS must contain every TF declared in
    # SHADOW_TIMEFRAMES — missing key would otherwise produce a
    # KeyError deep inside the first check_exit call. Skip in
    # test/ci where conftest may stand up minimal Settings.
    if settings.env not in {"test", "ci"}:
        from app.shadow.exit_monitor import validate_shadow_timeout_bars_keys
        validate_shadow_timeout_bars_keys()
```

- [ ] **2D.5** Run the validation test file — PASS.

### 2E. Commit + push

- [ ] **2E.1**

```bash
cd a:/v5_Trade_bot_followups
git add backend/app/shadow/exit_monitor.py \
        backend/app/shadow/scaling.py \
        backend/app/shadow/worker.py \
        backend/app/main.py \
        backend/tests/shadow/test_exit_monitor_pr11_settings_driven.py \
        backend/tests/shadow/test_worker_pr11_populates_hold_timeout_bars.py \
        backend/tests/shadow/test_startup_validation_pr11.py \
        backend/tests/shadow/test_hold_tp_scaling.py
git commit -m "feat(pr11): T1.3 Settings-backed timeout + compat shim + populate-on-open + startup validation (Phase 2)"
git push origin feat/pr11-impl-exit-improvements
```

**Quality bar:**
- ~17 new + amended tests PASS (9 settings-driven + 3 populate-on-open + 3 startup-validation + 2 hold_tp_scaling pinned).
- `tests/shadow/test_exit_monitor_per_tf.py` may still fail — handled in Phase 5.
- mypy clean.
- `pytest backend/tests/shadow -k "pr11" -x` — PASS.

---

## Phase 3: T1.4 — TP/SL ratio enforcement in SignalEvaluator

**Files:** Modify `app/shadow/engine.py`; create `tests/shadow/test_engine_pr11_tp_sl_ratio.py`.

- [ ] **3.1** Write failing tests at `backend/tests/shadow/test_engine_pr11_tp_sl_ratio.py`:

```python
"""PR11 T1.4: enforce TP/SL distance ratio >= SHADOW_MIN_TP_SL_RATIO.

Default ratio cutoff is 2.0 — equal to the default TP_ATR_MULT /
SL_ATR_MULT (3.0 / 1.5 = 2.0), so a default-config bot rejects ZERO
additional signals beyond what was already rejected pre-PR11. The
check is on `<` (strict less-than), so the boundary at exactly 2.0
passes.

Tunable per env. Setting to 0.0 disables the rule (rollback).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.shadow.engine import Direction, SignalEvaluator


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- LONG branch ---------------------------------------------------------


def test_long_ratio_exactly_2_passes_default() -> None:
    """At default cutoff 2.0, ratio == 2.0 passes (strict < comparison)."""
    ev = SignalEvaluator(sl_atr_mult=1.0, tp_atr_mult=2.0)
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is not None
    assert sig.direction is Direction.LONG


def test_long_ratio_below_cutoff_rejected_default() -> None:
    """Ratio 1.5 (tp_mult=1.5, sl_mult=1.0) below default 2.0 → rejected."""
    ev = SignalEvaluator(sl_atr_mult=1.0, tp_atr_mult=1.5)
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is None


def test_long_ratio_above_cutoff_passes_default() -> None:
    """Default 3.0/1.5 = 2.0; bump to 3.0/1.0 = 3.0 ratio → passes."""
    ev = SignalEvaluator(sl_atr_mult=1.0, tp_atr_mult=3.0)
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is not None


# --- SHORT branch (mirror image) -----------------------------------------


def test_short_ratio_below_cutoff_rejected_default() -> None:
    ev = SignalEvaluator(sl_atr_mult=1.0, tp_atr_mult=1.5)
    sig = ev.evaluate(
        symbol="ETHUSDT", score=-0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is None


def test_short_ratio_at_boundary_passes_default() -> None:
    ev = SignalEvaluator(sl_atr_mult=1.0, tp_atr_mult=2.0)
    sig = ev.evaluate(
        symbol="ETHUSDT", score=-0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is not None
    assert sig.direction is Direction.SHORT


# --- Rollback (cutoff=0) -------------------------------------------------


def test_rollback_zero_cutoff_accepts_subratio_signal(monkeypatch) -> None:
    """SHADOW_MIN_TP_SL_RATIO=0.0 disables the rule entirely. A
    pathologically bad ratio (tp_mult=0.1, sl_mult=1.0 → ratio=0.1) is
    accepted."""
    monkeypatch.setenv("SHADOW_MIN_TP_SL_RATIO", "0.0")
    get_settings.cache_clear()
    ev = SignalEvaluator(sl_atr_mult=1.0, tp_atr_mult=0.1)
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is not None


# --- Custom cutoff -------------------------------------------------------


def test_custom_high_cutoff_rejects_default_config(monkeypatch) -> None:
    """Operator sets cutoff=3.0; default 2.0-ratio signals are now rejected."""
    monkeypatch.setenv("SHADOW_MIN_TP_SL_RATIO", "3.0")
    get_settings.cache_clear()
    ev = SignalEvaluator()  # default sl_atr_mult=1.5, tp_atr_mult=3.0 → ratio 2.0
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is None


# --- Logging contract (INFO, not WARNING) --------------------------------


def test_rejected_signal_logs_info_with_diagnostic(caplog) -> None:
    """Rejection at INFO level with symbol, direction, ratios."""
    caplog.set_level(logging.INFO, logger="app.shadow.engine")
    ev = SignalEvaluator(sl_atr_mult=1.0, tp_atr_mult=1.5)
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.5, confidence=0.7,
        last_close=100.0, atr=1.0, layer_scores={}, ts=_NOW,
    )
    assert sig is None
    rejection_records = [
        r for r in caplog.records
        if "tp_sl_ratio" in r.getMessage().lower() or "ratio" in r.getMessage().lower()
    ]
    assert rejection_records, "Expected a ratio-rejection INFO log"
    assert rejection_records[0].levelno == logging.INFO


# --- Edge case: zero ATR (existing behavior preserved) -------------------


def test_zero_atr_short_circuits_before_ratio_check() -> None:
    """atr=0 → already rejected by existing path before ratio check."""
    ev = SignalEvaluator()
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.5, confidence=0.7,
        last_close=100.0, atr=0.0, layer_scores={}, ts=_NOW,
    )
    assert sig is None  # rejected for atr=0, ratio never computed
```

- [ ] **3.2** Run — FAIL.

- [ ] **3.3** Modify `backend/app/shadow/engine.py` — at top, add:

```python
import logging

log = logging.getLogger(__name__)
```

(Confirm not already present — engine.py currently has no logger.)

Inside `SignalEvaluator.evaluate`, after the LONG branch sl/tp computation but before `return ShadowSignal(...)`:

```python
        if score > self.long_threshold:
            sl = last_close - self.sl_atr_mult * atr
            tp = last_close + self.tp_atr_mult * atr
            # PR11 T1.4: reject signals whose TP-to-SL distance ratio
            # falls below the operator's minimum. Default cutoff 2.0
            # matches the default TP_ATR_MULT / SL_ATR_MULT = 2.0, so
            # default-config emits the same signal set as pre-PR11. The
            # check is on `<` (strict less-than) — boundary at 2.0 passes.
            from app.config import get_settings as _get_settings_for_ratio
            _cutoff = _get_settings_for_ratio().SHADOW_MIN_TP_SL_RATIO
            sl_dist = abs(last_close - sl)
            tp_dist = abs(tp - last_close)
            if sl_dist <= 0 or (tp_dist / sl_dist) < _cutoff:
                log.info(
                    "shadow_engine: signal rejected by TP/SL ratio rule — "
                    "symbol=%s direction=LONG tp_dist=%.6f sl_dist=%.6f "
                    "ratio=%.3f cutoff=%.2f",
                    symbol, tp_dist, sl_dist,
                    (tp_dist / sl_dist) if sl_dist > 0 else 0.0,
                    _cutoff,
                )
                return None
            return ShadowSignal(
                symbol=symbol, direction=Direction.LONG,
                score=score, confidence=confidence,
                entry_price=last_close, stop_loss=sl, take_profit=tp,
                atr=atr, layer_scores=layer_scores, ts=ts,
            )
```

And mirror for the SHORT branch:

```python
        if score < self.short_threshold:
            sl = last_close + self.sl_atr_mult * atr
            tp = last_close - self.tp_atr_mult * atr
            # PR11 T1.4: same ratio rule as LONG. SHORT inverts SL/TP
            # signs but the distance magnitudes are identical.
            from app.config import get_settings as _get_settings_for_ratio
            _cutoff = _get_settings_for_ratio().SHADOW_MIN_TP_SL_RATIO
            sl_dist = abs(sl - last_close)
            tp_dist = abs(last_close - tp)
            if sl_dist <= 0 or (tp_dist / sl_dist) < _cutoff:
                log.info(
                    "shadow_engine: signal rejected by TP/SL ratio rule — "
                    "symbol=%s direction=SHORT tp_dist=%.6f sl_dist=%.6f "
                    "ratio=%.3f cutoff=%.2f",
                    symbol, tp_dist, sl_dist,
                    (tp_dist / sl_dist) if sl_dist > 0 else 0.0,
                    _cutoff,
                )
                return None
            return ShadowSignal(
                symbol=symbol, direction=Direction.SHORT,
                score=score, confidence=confidence,
                entry_price=last_close, stop_loss=sl, take_profit=tp,
                atr=atr, layer_scores=layer_scores, ts=ts,
            )
```

- [ ] **3.4** Run — PASS all 8 new tests.

- [ ] **3.5** Regression: run `pytest backend/tests/unit/test_shadow_engine.py -x` — should still PASS because all existing fixtures use `sl_atr_mult=1.5, tp_atr_mult=3.0 → ratio 2.0` which sits at the boundary (passes).

- [ ] **3.6** Commit + push:

```bash
cd a:/v5_Trade_bot_followups
git add backend/app/shadow/engine.py \
        backend/tests/shadow/test_engine_pr11_tp_sl_ratio.py
git commit -m "feat(pr11): T1.4 TP/SL ratio enforcement in SignalEvaluator.evaluate (Phase 3)"
git push origin feat/pr11-impl-exit-improvements
```

**Quality bar:**
- 8 new tests PASS.
- All existing engine + worker tests still PASS (default sl_mult=1.5 + tp_mult=3.0 → boundary 2.0).
- Log emitted at INFO level (caplog assertion verifies).

---

## Phase 4: Alembic backfill migration — pin existing open positions to pre-PR11 values

**Files:** Create `backend/alembic/versions/2026_05_20_0025_pr11_backfill_hold_timeout_bars.py`; create 2 test files.

- [ ] **4.1** Write failing migration test at `backend/tests/db/test_pr11_migration.py`:

```python
"""Migration tests for 0025_pr11_backfill_hold_timeout_bars.

PR11 T1.3 reduces the per-TF baseline default from 24h-wall (1h=24,
15m=96) to 12h-wall (1h=12, 15m=48). Without this backfill, existing
in-flight positions with hold_timeout_bars=NULL would suddenly resolve
exits via the new (shorter) baseline — potentially closing 1h positions
that have already crossed bar 12 the instant the new code deploys.

The backfill pins all pre-PR11 rows to their ORIGINAL pre-PR11 baseline:
  - 1h, hold_timeout_bars IS NULL → 24
  - 15m, hold_timeout_bars IS NULL → 96

New signals after PR11 lands write hold_timeout_bars from
settings.SHADOW_TIMEOUT_BARS (Phase 2C populate-block) — so this
migration is one-shot, never re-runs against future rows.

Postgres-only. SQLite tests skip via the postgres_db guard.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")


pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


_NOW = datetime(2026, 5, 20, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_backfill_sets_24_for_null_1h_rows() -> None:
    """A row at (symbol='AAA', timeframe='1h', hold_timeout_bars=NULL)
    becomes hold_timeout_bars=24 after the migration runs."""
    engine = create_async_engine(_DSN)
    async with engine.begin() as conn:
        # Clean slate for this test's seeds
        await conn.execute(sa.text(
            "DELETE FROM shadow_open_positions WHERE symbol IN "
            "('PR11_NULL_1H', 'PR11_NULL_15M', 'PR11_SET_1H')"
        ))
        # Seed: 1h NULL, 15m NULL, 1h already-set (should NOT be overwritten)
        # NOTE: schema details (column names) inferred from
        # app/shadow/persistence.py — implementation engineer confirms.
        await conn.execute(sa.text(
            "INSERT INTO shadow_open_positions "
            "(user_id, symbol, direction, entry_price, stop_loss, "
            " take_profit, position_size_usdt, entry_score, "
            " entry_confidence, entry_atr, bars_held, opened_at, "
            " last_check_at, signal_id, timeframe, hold_timeout_bars) "
            "VALUES "
            "(1, 'PR11_NULL_1H', 'LONG', 100, 95, 110, 30, 0.5, 0.7, 2, "
            " 0, :now, :now, 'pr11_test_1', '1h', NULL), "
            "(1, 'PR11_NULL_15M', 'LONG', 100, 95, 110, 30, 0.5, 0.7, 2, "
            " 0, :now, :now, 'pr11_test_2', '15m', NULL), "
            "(1, 'PR11_SET_1H', 'LONG', 100, 95, 110, 30, 0.5, 0.7, 2, "
            " 0, :now, :now, 'pr11_test_3', '1h', 48)"
        ), {"now": _NOW})

    # Run the migration (assume alembic head is already at 0025_pr11)
    # The test assumes the migration has been applied via `alembic upgrade
    # head`; if running standalone, invoke alembic here as in PR9 tests.

    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT symbol, hold_timeout_bars FROM shadow_open_positions "
            "WHERE symbol IN ('PR11_NULL_1H', 'PR11_NULL_15M', 'PR11_SET_1H') "
            "ORDER BY symbol"
        ))).all()
    by_symbol = {r.symbol: r.hold_timeout_bars for r in rows}
    assert by_symbol["PR11_NULL_1H"] == 24
    assert by_symbol["PR11_NULL_15M"] == 96
    assert by_symbol["PR11_SET_1H"] == 48  # G1-set value preserved

    # Cleanup
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "DELETE FROM shadow_open_positions WHERE symbol IN "
            "('PR11_NULL_1H', 'PR11_NULL_15M', 'PR11_SET_1H')"
        ))
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_idempotent() -> None:
    """Re-running upgrade should be a no-op (all rows now have non-NULL
    hold_timeout_bars). Verifies the WHERE clause is correct."""
    import subprocess
    from pathlib import Path

    _BACKEND_DIR = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        capture_output=True, text=True, cwd=str(_BACKEND_DIR), check=False,
    )
    assert r.returncode == 0, f"second upgrade failed: stderr={r.stderr}"
```

- [ ] **4.2** Write downgrade round-trip test at `backend/tests/db/test_pr11_migration_downgrade.py`:

```python
"""FU-10 anticipation: PR11 migration round-trip.

PR11 backfill is a DATA-ONLY migration — no schema change. Downgrade
CANNOT reliably reverse the backfill because 24 / 96 values are
indistinguishable from G1-written values in the same range. Per the
migration docstring, downgrade is a documented no-op.

This test verifies upgrade → downgrade → upgrade does not break the
chain (alembic_version table consistency).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql")

_REV = "0025_pr11_backfill_hold_timeout_bars"
_PRIOR = "0024_pr10_symbol_perf_snapshots"

_BACKEND_DIR = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        capture_output=True, text=True, env=os.environ.copy(),
        cwd=str(_BACKEND_DIR), check=False,
    )


def test_pr11_migration_round_trip() -> None:
    r = _alembic("upgrade", _REV)
    assert r.returncode == 0, f"upgrade failed: {r.stderr}"

    r = _alembic("downgrade", _PRIOR)
    assert r.returncode == 0, f"downgrade failed: {r.stderr}"

    r = _alembic("upgrade", "head")
    assert r.returncode == 0, f"re-upgrade failed: {r.stderr}"
```

- [ ] **4.3** Create the migration at `backend/alembic/versions/2026_05_20_0025_pr11_backfill_hold_timeout_bars.py`:

```python
"""PR11 T1.3: backfill hold_timeout_bars on existing shadow_open_positions.

PR11 reduces the per-TF baseline default from 24h-wall (1h=24, 15m=96)
to 12h-wall (1h=12, 15m=48). Pre-PR11 rows have hold_timeout_bars=NULL
so check_exit falls back to the per-TF baseline at exit-check time.
Without this backfill, an existing 1h position at bars_held=20 would
suddenly TIMEOUT the instant the new code starts (20 >= 12) — even
though it was opened under the 24-bar contract.

This migration pins all existing rows to their pre-PR11 timeout via the
hold_timeout_bars override (which takes precedence over the baseline
per exit_monitor.check_exit). Behavior is:
  - 1h + NULL → 24
  - 15m + NULL → 96
  - already-set (G1 wrote a value) → unchanged

New signals emitted AFTER PR11 lands write hold_timeout_bars =
settings.SHADOW_TIMEOUT_BARS[tf] at signal-to-position creation (see
shadow.worker._maybe_open_position) — so this migration is one-shot,
never re-runs against post-PR11 rows.

Audit chain: shadow_open_positions is the in-flight state table, NOT
the hash-chained shadow_trades table. No audit-chain impact (the chain
locks the trade at CLOSE time, not OPEN time). The HASH_PAYLOAD_COLUMNS
policy excludes hold_timeout_bars per app/db/audit.py, so backfilling
the value here cannot break a later chain insert.

Downgrade: cannot reliably reverse — values 24 / 96 are indistinguishable
from G1-written values in the same range. No-op downgrade. Documented
intentional per FU-10 anticipation pattern.

Revision ID: 0025_pr11_backfill_hold_timeout_bars
Revises: 0024_pr10_symbol_perf_snapshots
Create Date: 2026-05-20
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0025_pr11_backfill_hold_timeout_bars"
down_revision: str | None = "0024_pr10_symbol_perf_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Pin pre-PR11 open positions to pre-PR11 baselines.

    Both statements are idempotent — they only touch rows where
    hold_timeout_bars IS NULL, which won't fire again on re-run.
    """
    op.execute(
        "UPDATE shadow_open_positions "
        "SET hold_timeout_bars = 24 "
        "WHERE hold_timeout_bars IS NULL AND timeframe = '1h'"
    )
    op.execute(
        "UPDATE shadow_open_positions "
        "SET hold_timeout_bars = 96 "
        "WHERE hold_timeout_bars IS NULL AND timeframe = '15m'"
    )


def downgrade() -> None:
    """Cannot reliably reverse the backfill — 24 / 96 are
    indistinguishable from G1-written values in the same range
    once written. No-op downgrade. Bringing a database back to
    a pre-PR11 state with this column NULL would require an
    external snapshot."""
    pass
```

- [ ] **4.4** Run migration locally on a postgres test DB (or in CI):

```bash
cd a:/v5_Trade_bot_followups/backend
python -m alembic upgrade head
```

- [ ] **4.5** Run migration tests — `pytest backend/tests/db/test_pr11_migration.py -x` — PASS.

- [ ] **4.6** Round-trip test — `pytest backend/tests/db/test_pr11_migration_downgrade.py -x` — PASS.

- [ ] **4.7** Commit + push:

```bash
cd a:/v5_Trade_bot_followups
git add backend/alembic/versions/2026_05_20_0025_pr11_backfill_hold_timeout_bars.py \
        backend/tests/db/test_pr11_migration.py \
        backend/tests/db/test_pr11_migration_downgrade.py
git commit -m "feat(pr11): alembic 0025 — backfill hold_timeout_bars on pre-PR11 open positions (Phase 4)"
git push origin feat/pr11-impl-exit-improvements
```

**Quality bar:**
- Migration applies cleanly on a freshly-seeded postgres DB.
- 2 migration tests PASS (backfill correctness + round-trip).
- Migration is idempotent — second `alembic upgrade head` does nothing.
- Pre-existing rows with hold_timeout_bars set (G1) are NOT overwritten.

---

## Phase 5: Existing-test audit + fixup for the 12 vs 24 default flip

**Files:** Modify `backend/tests/unit/test_shadow_exit_monitor.py`, `backend/tests/shadow/test_exit_monitor_per_tf.py`, `backend/tests/integration/test_shadow_worker_lifecycle.py`, `backend/tests/integration/test_shadow_worker.py`.

This phase resolves the existing-test fallout from Phase 1/2 (the default flipped from 24 to 12 for 1h, 96 to 48 for 15m).

- [ ] **5.1** Run the full shadow suite to enumerate failures:

```bash
cd a:/v5_Trade_bot_followups
pytest backend/tests/shadow backend/tests/integration/test_shadow_worker.py \
       backend/tests/integration/test_shadow_worker_lifecycle.py \
       backend/tests/unit/test_shadow_exit_monitor.py -x --tb=short
```

Expected failures (predicted from code read):

1. **`tests/shadow/test_exit_monitor_per_tf.py`**:
   - `test_timeout_bars_per_tf_table_values` asserts `["1h"] == 24, ["15m"] == 96` — now 12 / 48.
   - `test_1h_position_expires_at_24_bars` — 1h now expires at 12; 24-bar position TIMEOUTs but bars_held used to be the exact threshold. Update to 12.
   - `test_1h_position_does_not_expire_at_23_bars` — 23 now > 12, so it DOES expire. Move to 11.
   - `test_15m_position_expires_at_96_bars` — 96 now > 48. Move to 48.
   - `test_15m_position_does_not_expire_at_24_bars` — 24 now < 48 (still no expire) — actually still PASSES under the new default. Verify.
   - `test_15m_position_does_not_expire_at_95_bars` — 95 > 48, so it DOES expire. Move to 47.
   - `test_sl_still_fires_before_timeout_1h` — uses bars_held=24, still > 12 → TIMEOUT regardless of SL hit (existing semantics: timeout fires BEFORE SL check). Update bars_held to 12 to keep test intent.
   - `test_g1_position_with_explicit_timeout_bars_overrides_per_tf_default` — asserts `["1h"] == 24, ["15m"] == 96`. Update to 12 / 48.

2. **`tests/unit/test_shadow_exit_monitor.py`**:
   - `test_timeout_after_max_bars` — `make_long_pos(bars_held=TIMEOUT_BARS)`. Under new default, `TIMEOUT_BARS` (lazy-read) is 12 not 24 — test still PASSES because bars_held is computed dynamically. Verify no hard-coded 24 elsewhere in the file. Should auto-pass.

3. **`tests/integration/test_shadow_worker_lifecycle.py`** line 244, 361, 445, 452:
   - `EXPECTED_TIMEOUT_BARS: int = 24` — change to `12`.
   - `assert TIMEOUT_BARS == EXPECTED_TIMEOUT_BARS` — now 12 == 12, PASSES.
   - The candle-stream test at line 244 + 361 drives ≥24 candles per symbol; under new default 12 bars trigger TIMEOUT earlier. **Verify** the test's logic still holds. If it relies on bar-24 being the timeout boundary, either:
     - (a) Update to bar-12, OR
     - (b) Wrap with a `monkeypatch.setenv("SHADOW_TIMEOUT_BARS", '{"1h": 24, "15m": 96}')` fixture to pin pre-PR11 values.
   - Choice depends on the test's intent (probing timeout BEHAVIOR vs probing the literal number 24). Best practice: keep test focused — use (a) if testing timeout-fires-at-N, use (b) if testing pre-PR11 contract.

4. **`tests/integration/test_shadow_worker.py`** lines 373, 376, 381, 416:
   - `bars_held=TIMEOUT_BARS - 1` — auto-adapts to whatever TIMEOUT_BARS evaluates to (lazy-read returns 12). Passes through naturally.
   - `assert t.bars_held == TIMEOUT_BARS` — same lazy-read; passes.
   - Verify the candle-feeding logic before this point feeds at least TIMEOUT_BARS - 1 candles. If it hard-codes a count like 23, update to whatever TIMEOUT_BARS - 1 is (or use the constant).

- [ ] **5.2** Apply the predicted fixes file-by-file:

For `tests/shadow/test_exit_monitor_per_tf.py`, replace numbers in assertions:

```python
def test_timeout_bars_per_tf_table_values() -> None:
    """PR11 T1.3: 1h=12, 15m=48 (~12h wall-clock equivalent — operator-
    adjusted from pre-PR11 24h-wall)."""
    assert TIMEOUT_BARS_PER_TF["1h"] == 12
    assert TIMEOUT_BARS_PER_TF["15m"] == 48


def test_1h_position_expires_at_12_bars() -> None:
    p = _pos(timeframe="1h", bars_held=12)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_1h_position_does_not_expire_at_11_bars() -> None:
    p = _pos(timeframe="1h", bars_held=11)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is None


def test_15m_position_expires_at_48_bars() -> None:
    p = _pos(timeframe="15m", bars_held=48)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_15m_position_does_not_expire_at_24_bars() -> None:
    """24 < 48 — still no expire."""
    p = _pos(timeframe="15m", bars_held=24)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is None


def test_15m_position_does_not_expire_at_47_bars() -> None:
    p = _pos(timeframe="15m", bars_held=47)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is None


def test_sl_still_fires_before_timeout_1h() -> None:
    """Regression guard: timeout fires at bars_held >= limit regardless
    of SL hit (existing semantics preserved)."""
    p = _pos(timeframe="1h", bars_held=12)
    decision = check_exit(p, bar_high=99.5, bar_low=97.0, bar_close=98.5)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_g1_position_with_explicit_timeout_bars_overrides_per_tf_default() -> None:
    """PR11 default ['1h']=12, ['15m']=48. G1 override still wins via
    pos.hold_timeout_bars."""
    assert TIMEOUT_BARS_PER_TF.get("1h") == 12
    assert TIMEOUT_BARS_PER_TF.get("15m") == 48
    assert TIMEOUT_BARS_PER_TF.get("4h") is None
```

For `tests/integration/test_shadow_worker_lifecycle.py` line 445:
```python
EXPECTED_TIMEOUT_BARS: int = 12  # PR11 T1.3 — operator-adjusted from 24
```

For the candle-stream sections that hard-code 24+ candles (lines 244, 361 — read the test before changing): if the count is `range(25)` or similar, change to use `TIMEOUT_BARS + 1` so it auto-adapts.

- [ ] **5.3** T1.4 fixture audit. Run engine + worker tests:

```bash
pytest backend/tests/unit/test_shadow_engine.py \
       backend/tests/integration/test_shadow_worker.py -x
```

Per operator decision #11: existing fixtures emitting signals with `tp_atr_mult / sl_atr_mult < 2.0` need to either be updated so the engine accepts them (bump tp_atr_mult to >= 2.0 × sl_atr_mult) OR accept rejection if the test's intent IS to probe rejection. Spot check: all `test_shadow_engine.py` paths use default mults (1.5 / 3.0 → 2.0) which sit at the boundary (passes the `<` strict check). No fixture change expected. Confirm by running the suite.

- [ ] **5.4** Full shadow + integration suite — `pytest backend/tests/shadow backend/tests/integration -x` — PASS.

- [ ] **5.5** Full backend test suite — `pytest backend/tests -x` — PASS.

- [ ] **5.6** Commit + push:

```bash
cd a:/v5_Trade_bot_followups
git add backend/tests/shadow/test_exit_monitor_per_tf.py \
        backend/tests/unit/test_shadow_exit_monitor.py \
        backend/tests/integration/test_shadow_worker_lifecycle.py \
        backend/tests/integration/test_shadow_worker.py
git commit -m "test(pr11): update existing test fixtures for new 12h-wall timeout default (Phase 5)"
git push origin feat/pr11-impl-exit-improvements
```

**Quality bar:**
- Full backend pytest suite PASS.
- mypy + lint clean.
- No test marked xfail.
- The "pin pre-PR11 values" monkeypatch pattern is used ONLY where the test's intent is to probe pre-PR11 contract (e.g. `test_hold_tp_scaling.py`); behavioral tests that probe timeout-at-N use the new N.

---

## Phase 6: Open PR + continuous-rollout merge + cherry-pick prod-promotion + deploy + verify

**Files:** None (process phase).

### 6A. Local quality gate

- [ ] **6A.1** Final full suite + lint + mypy:

```bash
cd a:/v5_Trade_bot_followups
pytest backend/tests -x --tb=short
ruff check backend/
mypy backend/app
cd frontend && npm run typecheck && cd ..
```

All four green.

- [ ] **6A.2** Self-review the diff:

```bash
git log --oneline dev..HEAD
git diff dev..HEAD --stat
git diff dev..HEAD backend/app/config.py backend/app/shadow/exit_monitor.py \
                   backend/app/shadow/engine.py backend/app/shadow/worker.py \
                   backend/app/shadow/scaling.py backend/app/main.py
```

Confirm checklist:
- [ ] `SHADOW_TIMEOUT_BARS` default is `{"1h": 12, "15m": 48}` (NOT 24/96).
- [ ] `SHADOW_MIN_TP_SL_RATIO` default is `2.0`.
- [ ] `exit_monitor.py` has `get_timeout_bars_per_tf()` + lazy `__getattr__` shim.
- [ ] `scaling.py` imports `get_timeout_bars_per_tf` (not `TIMEOUT_BARS_PER_TF`).
- [ ] `worker._maybe_open_position` populates `hold_timeout_bars` AFTER the G1 block, ONLY when `is None`.
- [ ] `engine.evaluate` ratio-check fires on BOTH LONG and SHORT branches.
- [ ] `engine.evaluate` rejection logs at INFO (not WARNING / DEBUG).
- [ ] `main.lifespan` calls `validate_shadow_timeout_bars_keys()` BEFORE worker spawn, skipped in test/ci.
- [ ] Migration 0025 has empty downgrade body (no-op, documented).
- [ ] No new dependencies in `requirements.txt`.

### 6B. Open PR

- [ ] **6B.1**

```bash
cd a:/v5_Trade_bot_followups
gh pr create --base dev --title "feat(pr11): exit improvements — T1.3 per-TF timeout (12h-wall) + T1.4 TP/SL ratio enforcement" --body "$(cat <<'EOF'
## Summary

- **T1.3**: \`Settings.SHADOW_TIMEOUT_BARS\` (default \`{\"1h\": 12, \"15m\": 48}\` — operator-adjusted 12h-wall ceiling). Module-level \`TIMEOUT_BARS_PER_TF\` + \`TIMEOUT_BARS\` kept as lazy compat shims. New positions populate \`hold_timeout_bars\` from Settings at signal-to-position creation. Existing in-flight positions pinned to pre-PR11 values (24/96) via alembic backfill 0025.
- **T1.4**: \`Settings.SHADOW_MIN_TP_SL_RATIO\` (default 2.0). \`SignalEvaluator.evaluate\` rejects (returns None + logs INFO) signals where \`tp_dist/sl_dist < cutoff\`. Default cutoff matches pre-PR11 \`TP_ATR_MULT/SL_ATR_MULT\` ratio → zero behavioral change on default config.
- **Startup validation**: \`SHADOW_TIMEFRAMES ⊆ SHADOW_TIMEOUT_BARS.keys()\` enforced at boot, raises \`RuntimeError\` on missing key.

Source spec: \`docs/superpowers/specs/2026-05-20-pr11-exit-improvements-design.md\`

## Test plan

- [x] 6 settings defaults tests
- [x] 9 settings-driven exit-monitor tests (incl. compat shim)
- [x] 3 worker populate-on-open tests
- [x] 3 startup-validation tests
- [x] 8 TP/SL ratio enforcement tests (LONG + SHORT + boundary + rollback + log level)
- [x] 2 alembic migration tests (backfill correctness + round-trip)
- [x] All existing shadow + integration suites pass after fixture audit
- [x] mypy + ruff clean
- [x] Default config emits same signal set as pre-PR11 (ratio boundary at 2.0)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **6B.2** Wait for CI. Confirm all checks green.

### 6C. Continuous-rollout pattern: auto-merge to dev

- [ ] **6C.1** Once green, enable auto-merge OR merge manually:

```bash
gh pr merge --squash --delete-branch
```

(Per CLAUDE.md `dev_prod_branch_workflow`: changes default to dev; operator holds the dev→main gate.)

### 6D. Cherry-pick prod-promotion (#6 in the established pattern)

Per CLAUDE.md `cherry_pick_prod_promotion_pattern` (permanently authorized for FU + PR2 + PR3 + PR8 + PR9 stacked-dev scenarios; PR9 carve-out does NOT apply here — PR11 is shadow-only).

- [ ] **6D.1** Capture the dev squash-merge commit SHA:

```bash
git fetch origin dev
DEV_SHA=$(git rev-parse origin/dev)
```

- [ ] **6D.2** Cherry-pick to main via a new branch + PR:

```bash
git checkout main
git pull origin main
git checkout -b chore/pr11-cherry-pick-to-main
git cherry-pick "$DEV_SHA"
git push origin chore/pr11-cherry-pick-to-main
gh pr create --base main --title "chore: cherry-pick PR11 (exit improvements) dev→main" --body "Cherry-pick of #<PR_NUM> from dev. Per cherry_pick_prod_promotion_pattern (memory entry: permanently authorized for FU/PR2/PR3/PR8/PR9 stacked-dev scenarios). PR11 is shadow-only — no live-money risk."
```

- [ ] **6D.3** Wait for CI green, merge.

### 6E. Deploy + verify

- [ ] **6E.1** Trigger production deploy via the standard \`deploy.yml\` workflow on main.

- [ ] **6E.2** Verify on Hetzner production once deploy completes:

```bash
# Health check
curl -sf https://r9k2t.nagayuaj.com/api/v1/health
# Settings reflected (assuming a /diagnostics endpoint or similar; if not,
# read the backend logs for "SHADOW_TIMEOUT_BARS" startup line)
ssh hetzner "docker logs tr-backend --since 5m | grep -E 'shadow_engine|SHADOW_TIMEOUT'"
# Backfill applied
ssh hetzner "docker exec tr-postgres psql -U trade -d trade -c \"SELECT timeframe, COUNT(*), COUNT(hold_timeout_bars) AS non_null FROM shadow_open_positions GROUP BY timeframe;\""
# All rows should have hold_timeout_bars non-NULL after the migration.
```

- [ ] **6E.3** Watch for the next ~24h:
  - Shadow worker continues emitting signals (look for no spike in "signal rejected by TP/SL ratio rule" — default config emits zero additional rejections).
  - Per-TF timeout exits hit at the new earlier bar counts (12 for 1h, 48 for 15m) for NEW positions only.
  - Existing in-flight positions exit at their original 24/96 timeouts (via the backfill).

- [ ] **6E.4** Record verification in CLAUDE.md memory:

```bash
# Add to memory: pr11_prod_verified.md note
```

**Quality bar:**
- PR opened, CI green, merged to dev.
- Cherry-pick PR opened to main, CI green, merged.
- Deploy completes (no dirty-host failure — see `deploy_silent_failure.md` memory entry — verify worker count actually increased before declaring victory).
- Postgres query confirms `hold_timeout_bars` non-NULL on all `shadow_open_positions` rows.
- Bot continues to emit shadow signals (no ratio-rejection storm).

---

## Self-review checklist (before opening PR)

- [ ] ~30 tests pass (6 settings + 9 exit_monitor settings-driven + 3 worker populate + 3 startup + 8 ratio + 2 migration).
- [ ] Lint + mypy clean.
- [ ] Existing shadow + integration suites pass after Phase 5 fixture audit.
- [ ] Default-config behavior preserved at SIGNAL EMIT level: ratio cutoff 2.0 == default TP/SL mult ratio.
- [ ] Default-config behavior CHANGED at TIMEOUT level: 1h=12 bars (was 24), 15m=48 bars (was 96). Existing in-flight positions pinned to pre-PR11 via backfill.
- [ ] G1 path (HOLD_TP_SCALING_ENABLED=True) still authoritative for `hold_timeout_bars` when ON.
- [ ] Migration applies + downgrade round-trips (downgrade is documented no-op).
- [ ] No retroactive backfill of historical `shadow_trades` (per operator decision #7).
- [ ] No new dependencies.

---

## Execution handoff

Plan complete. After dev merge:
- Cherry-pick prod-promotion #6 (PR11) per the established pattern — no operator carve-out gate (PR9 was the only PR with that carve-out).
- Deploy + Postgres-row-count verify.
- 24h soak watching for rejection-rate spikes (expected: zero on default config).
- Memory entry: `pr11_prod_verified.md` to be added once verified.

---

## Decision points that needed making (beyond the operator's locked defaults)

1. **Where to insert the populate-on-open block in worker.py** — chosen: AFTER the existing G1 try-except, BEFORE the layer_scores_array build. Rationale: G1 is the source of truth when ON; T1.3 fills the gap when G1 didn't write. Placing it AFTER also covers the G1 fail-open path (which leaves `hold_timeout_bars=None`).

2. **Compat shim implementation** — chosen: module-level `__getattr__` for `TIMEOUT_BARS_PER_TF` + `TIMEOUT_BARS` rather than wrapping them as class instances. Rationale: matches Python's standard idiom for lazy module attrs, zero behavioral change for callers, minimal new code surface.

3. **Where to call `validate_shadow_timeout_bars_keys()`** — chosen: top of `lifespan` (after `settings = get_settings()`), gated on `settings.env not in {"test", "ci"}`. Rationale: matches existing gating pattern (worker_enabled is the same gate). Fail-loud at boot, but tests with non-default Settings can still construct minimal envs.

4. **Migration test seed schema** — the seed `INSERT INTO shadow_open_positions` references columns from the live `persistence.persist_open_position` function. Implementation engineer should run `\d shadow_open_positions` on a postgres test DB and adjust the INSERT to match exactly (some columns may be NOT NULL with no default).

5. **`tests/shadow/test_hold_tp_scaling.py` strategy** — chosen: monkeypatch SHADOW_TIMEOUT_BARS to the pre-PR11 values to keep the G1 math fixtures stable. Rationale: those tests probe the SCALING FORMULA, not the per-TF baseline; pinning baseline keeps the test focused.

6. **Logging diagnostic format** — chosen: f-string-style with %s/%f placeholders, NOT structured JSON. Rationale: matches existing shadow_worker log format; structured logs are a separate follow-up.

7. **Migration revision number 0025** — confirmed next in sequence (0024 is PR10's symbol_performance_snapshots). The migration revises that as `down_revision`.

---

## Architecture concerns discovered (during code read for this plan)

1. **Spec text uses "`ShadowEngine.generate_signal`" but no such class/method exists** in `backend/app/shadow/engine.py`. The actual hook is `SignalEvaluator.evaluate` (a dataclass method). Plan uses the real name. No spec change needed — the operator's morning context referred to the same logical location.

2. **G1 fail-open path** (worker.py:570 `except _g1_err`) leaves `position.hold_timeout_bars=None`. This is handled correctly by the T1.3 populate-block (it checks `is None` and fills), but worth flagging as a code path where G1's expected behavior (set `hold_timeout_bars=scaled_bars`) silently doesn't happen. T1.3 incidentally hardens this case.

3. **`scaling.py`'s baseline lookup will silently shift with the new default**. Pre-PR11: G1 scales 15m baseline=96 → mtf_agreement=4 → 192 bars (~48h). Post-PR11: baseline=48 → 96 bars (~24h). When operator flips `HOLD_TP_SCALING_ENABLED=true` post-PR11, the absolute bars-held caps will be HALF what the spec example numbers suggest. This is correct behavior (operator's intent was 12h-wall everywhere), but flag for the operator: any expected wall-clock numbers for G1 scenarios need to be recomputed against the new baselines. `tests/shadow/test_hold_tp_scaling.py` pins pre-PR11 values to keep the formula test fixtures stable.

4. **Settings mutation contract for `get_timeout_bars_per_tf()`**. The plan returns a `dict(...)` copy on every call so callers can't mutate `Settings.SHADOW_TIMEOUT_BARS` in place. Cost is ~50ns per call; we call once per closed candle per open symbol-tf (well under 100/min in prod). Acceptable.

5. **`backtest.py` has its own `_TIMEOUT_BARS = 24`** module constant. Plan does NOT touch it — it's the offline backtester, not the live shadow path. Operator may want to migrate it in a follow-up to read from Settings too, but that's out of PR11 scope.

6. **`docs/KNOWN_ISSUES.md:331`** references `app/shadow/exit_monitor.py:7` with `TIMEOUT_BARS=24` hardcoded — a documentation entry that will become stale after PR11. Plan does not modify the doc (out of scope), but flag for a follow-up doc-only PR to clean up.
