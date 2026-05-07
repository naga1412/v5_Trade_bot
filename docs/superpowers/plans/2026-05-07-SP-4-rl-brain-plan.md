# SP-4 RL Brain Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the SP-4 RL Brain (L10) in 5 phases per the design spec, starting with offline data plumbing (Phase A) then layering training, inference, promotion, and Telegram approval.

**Architecture:** PPO policy with per-asset 32-dim embeddings; trains nightly on Colab T4 from a 365-day replay buffer; integrates into `app.predictor.build_prediction` as the L10 aggregation layer; degrades gracefully to equal-weight when no checkpoint is active.

**Tech Stack:** PyTorch 2.4 (existing), TimescaleDB+Alembic (existing), FastAPI admin routes (mirror SP-1 admin_ml.py), python-telegram-bot (NEW), Colab T4 GPU.

**Reference:** `docs/superpowers/specs/2026-05-07-SP-4-rl-brain-design.md` is the design source-of-truth. When this plan and the spec disagree, the spec wins.

---

## Phase A — Offline data plumbing

**Phase exit criteria:** Replay buffer materializes 365d of past `shadow_trades` into typed `Transition` records; observation builder produces a stable 57-float vector; reward function matches by-hand calculation on 20 fixtures. All without any GPU, training, or model weights involved.

### Task A1: Migration 0015 — rl_checkpoints + brain_decisions tables

**Files:**
- Create: `backend/migrations/versions/2026_05_07_0015_rl_checkpoints_and_brain_decisions.py`
- Create: `backend/tests/unit/test_migration_0015_rl_tables.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_migration_0015_rl_tables.py
"""Smoke test for migration 0015 (SP-4 Phase A1)."""
from sqlalchemy import inspect

import pytest
from alembic.config import Config
from alembic import command


@pytest.mark.asyncio
async def test_migration_0015_creates_rl_tables(test_engine, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0015")
    insp = inspect(test_engine.sync_engine)
    tables = set(insp.get_table_names())
    assert "rl_checkpoints" in tables
    assert "brain_decisions" in tables
    cols = {c["name"] for c in insp.get_columns("rl_checkpoints")}
    assert {"id", "model_name", "version", "checkpoint_uri", "sha256",
            "trained_at", "is_active", "eval_results"} <= cols
    cols = {c["name"] for c in insp.get_columns("brain_decisions")}
    assert {"id", "ts", "symbol", "checkpoint_id", "observation",
            "action", "smoothed_action", "prev_hash", "row_hash"} <= cols
    indexes = {i["name"] for i in insp.get_indexes("rl_checkpoints")}
    assert "rl_checkpoints_one_active" in indexes


@pytest.mark.asyncio
async def test_migration_0015_downgrades_cleanly(test_engine, alembic_config) -> None:
    command.upgrade(alembic_config, "0015")
    command.downgrade(alembic_config, "0014")
    insp = inspect(test_engine.sync_engine)
    assert "rl_checkpoints" not in insp.get_table_names()
    assert "brain_decisions" not in insp.get_table_names()
```

- [ ] **Step 2: Run test to verify it fails (migration doesn't exist yet)**

Run: `cd backend && pytest tests/unit/test_migration_0015_rl_tables.py -v`
Expected: FAIL with "Path not found" or "alembic.util.exc.CommandError: Can't locate revision identified by '0015'"

- [ ] **Step 3: Write the migration**

```python
# backend/migrations/versions/2026_05_07_0015_rl_checkpoints_and_brain_decisions.py
"""SP-4 Phase A1 — RL Brain checkpoint registry + decision audit trail.

Mirrors the SP-1 ml_checkpoints / SP-0 predictions pattern: registry table
for trained PPO checkpoints + append-only hash-chained log of every
brain decision in production for replay/debug.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rl_checkpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("checkpoint_uri", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("train_data_window", sa.Text, nullable=False),
        sa.Column("eval_results", postgresql.JSONB, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.UniqueConstraint("model_name", "version"),
    )
    op.create_index(
        "rl_checkpoints_one_active",
        "rl_checkpoints",
        ["model_name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "brain_decisions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("checkpoint_id", sa.Integer,
                  sa.ForeignKey("rl_checkpoints.id"), nullable=False),
        sa.Column("observation", postgresql.JSONB, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("action_logits", postgresql.JSONB, nullable=False),
        sa.Column("value_estimate", sa.Float, nullable=True),
        sa.Column("smoothed_action", sa.Text, nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
    )
    op.create_index(
        "brain_decisions_symbol_ts", "brain_decisions",
        ["symbol", sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_index("brain_decisions_symbol_ts", table_name="brain_decisions")
    op.drop_table("brain_decisions")
    op.drop_index("rl_checkpoints_one_active", table_name="rl_checkpoints")
    op.drop_table("rl_checkpoints")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_migration_0015_rl_tables.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/2026_05_07_0015_rl_checkpoints_and_brain_decisions.py \
        backend/tests/unit/test_migration_0015_rl_tables.py
git commit -m "feat(sp-4): migration 0015 — rl_checkpoints + brain_decisions"
```

---

### Task A2: Observation builder

**Files:**
- Create: `backend/app/rl/__init__.py`
- Create: `backend/app/rl/obs.py`
- Create: `backend/tests/unit/test_rl_obs.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_rl_obs.py
"""Tests for app.rl.obs.build_observation (SP-4 Phase A2)."""
import numpy as np
import pytest

from app.rl.obs import (
    OBS_DIM, AssetState, MacroFeatures, MarketFeatures, PositionState,
    build_observation, encode_regime,
)


def _market() -> MarketFeatures:
    return MarketFeatures(
        atr_pct=0.02, funding_rate=0.0001, oi_delta_24h=0.05,
        dxy_corr_30d=-0.3, gold_corr_30d=0.1,
        regime="bull_breakout",
    )


def _position() -> PositionState:
    return PositionState(cur_position=0, unrealized_pnl_R=0.0, bars_in_position=0)


def _macro() -> MacroFeatures:
    return MacroFeatures(
        hours_to_next_high_impact=12.0, fomc_window=False,
        weekend=False, asia_open=True,
    )


def test_observation_has_correct_dimension() -> None:
    asset = AssetState(asset_id=0, embedding=np.zeros(32, dtype=np.float32))
    layer_scores = (0.1, -0.2, 0.3, 0.0, 0.5, -0.1, 0.2, 0.4, -0.3)
    obs = build_observation(asset, layer_scores, _market(), _position(), _macro())
    assert obs.shape == (OBS_DIM,)
    assert obs.dtype == np.float32


def test_observation_dimension_constant_matches_spec() -> None:
    """Spec sec D1: 32 (emb) + 9 (layers) + 9 (market) + 3 (pos) + 4 (macro) = 57."""
    assert OBS_DIM == 57


def test_layer_scores_required_to_be_9() -> None:
    asset = AssetState(asset_id=0, embedding=np.zeros(32, dtype=np.float32))
    with pytest.raises(ValueError, match="9 layer scores"):
        build_observation(
            asset, (0.1,) * 8,  # only 8
            _market(), _position(), _macro(),
        )


def test_embedding_dimension_required_to_be_32() -> None:
    asset = AssetState(asset_id=0, embedding=np.zeros(16, dtype=np.float32))
    with pytest.raises(ValueError, match="32-dim"):
        build_observation(
            asset, (0.0,) * 9, _market(), _position(), _macro(),
        )


def test_regime_one_hot_encoding() -> None:
    """All 5 regime names map to a unique one-hot index in [0,4]."""
    seen = set()
    for r in ("bull_breakout", "bear_crash", "sideways_grind",
              "high_volatility", "low_volatility"):
        v = encode_regime(r)
        assert v.shape == (5,)
        assert v.sum() == 1.0
        seen.add(int(np.argmax(v)))
    assert len(seen) == 5


def test_unknown_regime_falls_back_to_zero_vector() -> None:
    v = encode_regime("alien_regime")
    assert v.shape == (5,)
    assert v.sum() == 0.0


def test_observation_is_deterministic_for_same_inputs() -> None:
    asset = AssetState(asset_id=7, embedding=np.arange(32, dtype=np.float32) / 100)
    args = (asset, (0.1, 0.2, -0.1, 0.0, 0.4, -0.3, 0.5, -0.2, 0.0),
            _market(), _position(), _macro())
    a = build_observation(*args)
    b = build_observation(*args)
    assert np.array_equal(a, b)
```

- [ ] **Step 2: Run tests to verify they fail (module doesn't exist)**

Run: `cd backend && pytest tests/unit/test_rl_obs.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'app.rl'"

- [ ] **Step 3: Write the implementation**

```python
# backend/app/rl/__init__.py
"""SP-4 RL Brain (L10) — package root.

See: docs/superpowers/specs/2026-05-07-SP-4-rl-brain-design.md
"""
```

```python
# backend/app/rl/obs.py
"""Observation builder for the SP-4 PPO policy.

Spec sec D1 — assembles a 57-float vector that the policy network sees on
each prediction tick:

    asset_embedding (32)  +
    layer_scores L1..L9 (9)  +
    market_state (9: ATR%, funding, OI Δ24h, DXY corr, gold corr,
                     regime one-hot[5])  +
    position_state (3: cur_pos {-1,0,+1}, unrealized_pnl_R, bars_in_position)  +
    macro_calendar (4: hours_to_next_HI, FOMC_window, weekend, asia_open)
    = 57

Same function runs at training-time (replay buffer) and inference-time
(production) — that's the spec sec 8 cross-cutting policy on
training-serving skew.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np


OBS_DIM: int = 57
EMB_DIM: int = 32
N_LAYER_SCORES: int = 9
REGIME_NAMES: tuple[str, ...] = (
    "bull_breakout", "bear_crash", "sideways_grind",
    "high_volatility", "low_volatility",
)


RegimeName = Literal[
    "bull_breakout", "bear_crash", "sideways_grind",
    "high_volatility", "low_volatility",
]


@dataclass(frozen=True)
class AssetState:
    asset_id: int
    embedding: np.ndarray  # shape (32,), float32


@dataclass(frozen=True)
class MarketFeatures:
    atr_pct: float
    funding_rate: float
    oi_delta_24h: float
    dxy_corr_30d: float
    gold_corr_30d: float
    regime: str  # one of REGIME_NAMES (or unknown -> zero vector)


@dataclass(frozen=True)
class PositionState:
    cur_position: int        # {-1, 0, +1}
    unrealized_pnl_R: float
    bars_in_position: int


@dataclass(frozen=True)
class MacroFeatures:
    hours_to_next_high_impact: float
    fomc_window: bool
    weekend: bool
    asia_open: bool


def encode_regime(name: str) -> np.ndarray:
    """5-vector one-hot encoding; unknown name -> zeros."""
    out = np.zeros(5, dtype=np.float32)
    if name in REGIME_NAMES:
        out[REGIME_NAMES.index(name)] = 1.0
    return out


def build_observation(
    asset: AssetState,
    layer_scores: Sequence[float],
    market: MarketFeatures,
    position: PositionState,
    macro: MacroFeatures,
) -> np.ndarray:
    """Assemble a single (57,) float32 observation vector. See module docstring."""
    if len(layer_scores) != N_LAYER_SCORES:
        raise ValueError(
            f"expected 9 layer scores (L1..L9), got {len(layer_scores)}"
        )
    if asset.embedding.shape != (EMB_DIM,):
        raise ValueError(
            f"expected 32-dim asset embedding, got shape {asset.embedding.shape}"
        )

    parts: list[np.ndarray] = [
        asset.embedding.astype(np.float32),
        np.asarray(layer_scores, dtype=np.float32),
        np.array([
            market.atr_pct, market.funding_rate, market.oi_delta_24h,
            market.dxy_corr_30d, market.gold_corr_30d,
        ], dtype=np.float32),
        encode_regime(market.regime),
        np.array([
            float(position.cur_position),
            float(position.unrealized_pnl_R),
            float(position.bars_in_position),
        ], dtype=np.float32),
        np.array([
            float(macro.hours_to_next_high_impact),
            float(macro.fomc_window),
            float(macro.weekend),
            float(macro.asia_open),
        ], dtype=np.float32),
    ]
    obs = np.concatenate(parts)
    if obs.shape != (OBS_DIM,):  # pragma: no cover — invariant check
        raise RuntimeError(
            f"obs assembly bug: got shape {obs.shape}, expected ({OBS_DIM},)"
        )
    return obs


__all__ = [
    "OBS_DIM", "EMB_DIM", "N_LAYER_SCORES", "REGIME_NAMES",
    "AssetState", "MarketFeatures", "PositionState", "MacroFeatures",
    "RegimeName", "encode_regime", "build_observation",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_rl_obs.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/rl/__init__.py backend/app/rl/obs.py \
        backend/tests/unit/test_rl_obs.py
git commit -m "feat(sp-4): observation builder (57-dim vector)"
```

---

### Task A3: Reward function

**Files:**
- Create: `backend/app/rl/reward.py`
- Create: `backend/tests/unit/test_rl_reward.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_rl_reward.py
"""Tests for app.rl.reward.compute_reward (SP-4 Phase A3)."""
from dataclasses import dataclass

import numpy as np
import pytest

from app.rl.reward import RECENT_TRADES_WINDOW, compute_reward


@dataclass(frozen=True)
class FakeTrade:
    pnl_quote: float
    initial_risk_quote: float


def test_positive_R_no_history_uses_prior_sigma() -> None:
    """With <5 trades, sigma defaults to 1.0; reward = R - 0.5*1.0."""
    trade = FakeTrade(pnl_quote=200.0, initial_risk_quote=100.0)  # +2R
    r = compute_reward(trade, recent=[])
    assert r == pytest.approx(2.0 - 0.5)


def test_negative_R_clipped_to_minus_three() -> None:
    trade = FakeTrade(pnl_quote=-1000.0, initial_risk_quote=100.0)  # -10R
    r = compute_reward(trade, recent=[])
    assert r == -3.0


def test_positive_R_clipped_to_plus_three() -> None:
    trade = FakeTrade(pnl_quote=1000.0, initial_risk_quote=100.0)  # +10R
    r = compute_reward(trade, recent=[])
    assert r == 3.0


def test_sigma_computed_from_at_least_five_recent_trades() -> None:
    recent = [FakeTrade(pnl_quote=100, initial_risk_quote=100) for _ in range(3)]  # all +1R
    recent += [FakeTrade(pnl_quote=-100, initial_risk_quote=100) for _ in range(3)]  # all -1R
    # std of [1, 1, 1, -1, -1, -1] = 1.0
    trade = FakeTrade(pnl_quote=200.0, initial_risk_quote=100.0)  # +2R
    r = compute_reward(trade, recent=recent)
    expected = 2.0 - 0.5 * 1.0
    assert r == pytest.approx(expected, abs=0.01)


def test_sigma_uses_only_last_n_trades() -> None:
    recent = [FakeTrade(pnl_quote=10000, initial_risk_quote=100) for _ in range(50)]
    recent += [FakeTrade(pnl_quote=100, initial_risk_quote=100) for _ in range(RECENT_TRADES_WINDOW)]
    trade = FakeTrade(pnl_quote=200.0, initial_risk_quote=100.0)
    r = compute_reward(trade, recent=recent)
    # Sigma should reflect the last RECENT_TRADES_WINDOW trades (all +1R), so sigma=0
    assert r == pytest.approx(2.0)


def test_zero_risk_trade_raises() -> None:
    trade = FakeTrade(pnl_quote=100.0, initial_risk_quote=0.0)
    with pytest.raises(ValueError, match="initial_risk_quote must be > 0"):
        compute_reward(trade, recent=[])
```

- [ ] **Step 2: Run tests — should fail (no module)**

Run: `cd backend && pytest tests/unit/test_rl_reward.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
# backend/app/rl/reward.py
"""Per-trade risk-adjusted reward for the SP-4 PPO policy.

Spec sec 4 — reward = clip(realized_R - 0.5 * sigma_R, -3, +3).
"""
from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np


RECENT_TRADES_WINDOW: int = 20
RISK_AVERSION_WEIGHT: float = 0.5
PRIOR_SIGMA: float = 1.0
REWARD_CLIP: float = 3.0
MIN_TRADES_FOR_SIGMA: int = 5


class _TradeLike(Protocol):
    pnl_quote: float
    initial_risk_quote: float


def compute_reward(trade: _TradeLike, *, recent: Sequence[_TradeLike]) -> float:
    """Compute risk-adjusted reward in R-multiples.

    `recent` is the trailing list of CLOSED trades on the same asset, oldest
    first. Only the last RECENT_TRADES_WINDOW are used to estimate sigma.
    """
    if trade.initial_risk_quote <= 0:
        raise ValueError(
            f"initial_risk_quote must be > 0, got {trade.initial_risk_quote}"
        )
    realized_R = trade.pnl_quote / trade.initial_risk_quote

    if len(recent) >= MIN_TRADES_FOR_SIGMA:
        slice_ = list(recent)[-RECENT_TRADES_WINDOW:]
        Rs = np.array([
            t.pnl_quote / t.initial_risk_quote for t in slice_
            if t.initial_risk_quote > 0
        ], dtype=np.float64)
        sigma = float(np.std(Rs)) if Rs.size > 0 else PRIOR_SIGMA
    else:
        sigma = PRIOR_SIGMA

    raw = realized_R - RISK_AVERSION_WEIGHT * sigma
    return float(np.clip(raw, -REWARD_CLIP, REWARD_CLIP))


__all__ = [
    "RECENT_TRADES_WINDOW", "RISK_AVERSION_WEIGHT", "PRIOR_SIGMA",
    "REWARD_CLIP", "MIN_TRADES_FOR_SIGMA", "compute_reward",
]
```

- [ ] **Step 4: Run tests — should pass**

Run: `cd backend && pytest tests/unit/test_rl_reward.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/rl/reward.py backend/tests/unit/test_rl_reward.py
git commit -m "feat(sp-4): per-trade risk-adjusted reward function"
```

---

### Task A4: Replay buffer (load_from_shadow_trades)

**Files:**
- Create: `backend/app/rl/replay_buffer.py`
- Create: `backend/tests/unit/test_rl_replay_buffer.py`

This task is the largest in Phase A — it does the point-in-time joins between `shadow_trades`, `predictions`, `intermarket_snapshots`, and reconstructs the observation vector for the trade's `opened_at` timestamp.

- [ ] **Step 1: Write failing tests with minimal fixture-based coverage**

(Test file provided in the spec; ~150 LOC; uses the existing `shadow_trades` fixture from `backend/tests/conftest.py`. See spec sec 5.1 for the join logic.)

- [ ] **Step 2: Run — fails**

- [ ] **Step 3: Implement** — `backend/app/rl/replay_buffer.py` with:
  - `Transition` dataclass (obs, action_taken, reward, next_obs, done)
  - `async def load_from_shadow_trades(session, *, window_days=365) -> list[Transition]`
  - Point-in-time joins on `predictions` (layer scores), `intermarket_snapshots` (funding/OI), `regime_markers` (current regime)
  - Action recovered from `shadow_trades.direction` + `shadow_trades.size_fraction` mapped to one of 5 discrete actions
  - Reward computed via `app.rl.reward.compute_reward`

- [ ] **Step 4: Run — passes**

- [ ] **Step 5: Commit**

---

### Task A5: Asset embedding adapter + cold-start blending

**Files:**
- Create: `backend/app/rl/adapter.py`
- Create: `backend/tests/unit/test_rl_adapter.py`

- [ ] Steps 1-5 (same TDD pattern):

Implements:
- `AssetEmbeddingTable` — wraps `nn.Embedding(N_assets, 32)` with a `register_asset(symbol)` method that adds new entries seeded from the median of existing embeddings
- `cold_start_blend(median_emb, learned_emb, n_trades)` — returns `(1-α)*median + α*learned` where `α = min(1.0, n_trades/100)` per spec sec Q5
- `serialize` / `deserialize` for checkpoint persistence

---

### Task A6: rl_checkpoints loader (mirror of app.ml.checkpoints)

**Files:**
- Create: `backend/app/rl/checkpoints.py`
- Create: `backend/tests/unit/test_rl_checkpoints.py`

- [ ] Steps 1-5:

Mirrors `backend/app/ml/checkpoints.py` 1:1 except table name + dataclass fields. Same `file://` / `s3://` / `b2://` URI scheme support. Same lifespan-loaded module-state pattern.

---

### Task A7: Phase A integration test + final commit

**Files:**
- Create: `backend/tests/integration/test_sp4_phase_a_integration.py`

- [ ] **Step 1**: write a single integration test that:
  1. Applies migration 0015
  2. Inserts 5 fake `shadow_trades` rows with their associated `predictions` rows
  3. Runs `replay_buffer.load_from_shadow_trades(...)` and asserts 5 transitions returned
  4. Asserts every transition has obs.shape == (57,), reward in [-3, +3], action in valid set

- [ ] **Step 2**: run, see fails

- [ ] **Step 3**: fix any wiring gaps surfaced

- [ ] **Step 4**: run, passes

- [ ] **Step 5**: commit + push branch

---

## Phase B — PPO trainer + Colab notebook

(Detailed task list deferred until Phase A ships and the user has reviewed it. Phase B parallels SP-1.1's `tools/ml/train.py` pattern: builds a `PolicyNetwork`, runs PPO-clip rollouts on the replay buffer, evaluates against equal-weight baseline on a 6-month backtest, saves `.pt` + eval JSON. See spec sec 5.2.)

---

## Phase C — Inference integration

(Detailed task list deferred until Phase B ships. Phase C wires the brain into `app/predictor.py` with graceful degradation, adds the `brain_decisions` write path with hash-chaining, and implements the safety guards from spec sec 7.)

---

## Phase D — Admin endpoints + champion-challenger gate

(Detailed task list deferred until Phase C ships. Phase D adds `/api/v1/admin/rl-checkpoints` mirroring `admin_ml.py`, plus extends SP-7's `evaluate_challenger` to handle `metric="sharpe"` for L10. See spec sec 5.4.)

---

## Phase E — Telegram approval flow

(Detailed task list deferred until Phase D ships. Phase E builds the inline-button approval bot per spec sec 5.4 with a 7-day timeout fallback.)

---

## Self-review checklist

Before marking the plan complete:

- [ ] Every Phase A task has Step 1 (failing test), Step 2 (verify fail), Step 3 (impl), Step 4 (verify pass), Step 5 (commit) — TDD
- [ ] No "TBD" / "TODO" / placeholder text inside Phase A tasks
- [ ] Type signatures referenced in later tasks (Transition, PolicyNetwork, etc.) match what earlier tasks define
- [ ] Phase A is fully decoupled from GPU / Colab / SP-1.1 — runs entirely on CPU + dev DB
- [ ] Spec is the source of truth for design decisions; this plan defers to spec
