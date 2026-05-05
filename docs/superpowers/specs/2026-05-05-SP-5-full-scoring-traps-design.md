# SP-5 — Full Scoring + Traps Design Spec

**Date:** 2026-05-05
**Status:** Approved (autonomous-mode default; user can redirect)
**Implementation target:** Sub-project SP-5 (after SP-2 + SP-3 ship; both done)
**Depends on:** SP-2 (158 patterns + L2 layer + 43 indicators), SP-3 (4 exchange adapters + universe_history)
**Companion specs:** `2026-05-01-trading-radar-meta-plan-design.md` §2.3, `MASTER_PLAN.md` §6 (12 traps)

---

## 1. Purpose

Add the missing scoring layers (L4 SMC + L6 micro-patterns + L8 Conv-LSTM hookup) plus the **12-trap filter system + 5 short-only filters**, **asymmetric long/short thresholds**, and the full **FINAL_SCORE formula** that combines static layers, brain adjustment, traps, and news multiplier into a single number that drives the tier classification (NO_SIGNAL / PAPER / SMALL / STANDARD / A+).

After SP-5 ships, the bot has a complete scoring engine that produces actionable trade signals with quantified confidence. The remaining sub-projects (SP-4 RL brain, SP-1.1 trained Conv-LSTM, SP-9 news/sentiment) populate placeholder slots that SP-5 already wires.

### Non-goals

- **No L7 XGBoost layer** — deferred to SP-1.5 (parallel ML track)
- **No L9 News + sentiment** — deferred to SP-9 (FinBERT is heavy; needs its own brainstorm)
- **No L10 RL brain training** — deferred to SP-4
- **No new UI surface** — scoring outputs land in `predictions.layer_scores` JSONB; UI panels (the 14 sidebar panels per master plan §9) are SP-6
- **No backtest framework** — SP-7 territory
- **No live trading logic** — SP-8 territory; SP-5 produces signals only

---

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Scoring layer count | **9 static layers (L1–L9) + 1 RL layer (L10)** per meta-plan §2.3 |
| 2 | L2 mapping | **L2 = unified 158-pattern voting** (already shipped in SP-2; supersedes MASTER_PLAN's L2 SMC label since SMC patterns can be added to the L2 library as needed) |
| 3 | L4 (NEW) | **Smart Money Concepts** — BOS, CHoCH, Order Blocks, Fair Value Gaps, Liquidity Sweeps. Implements the spirit of MASTER_PLAN's "L2 SMC" within the new layer numbering. |
| 4 | L6 (NEW) | **Micro-pattern aggregator** — high-frequency single-bar patterns + reaction patterns at key levels (separate from L2's general 158-pattern set; tuned for 1m/5m timeframes) |
| 5 | L7 placeholder | **Returns None** — SP-1.5 will populate with XGBoost on engineered features |
| 6 | L8 (Conv-LSTM hookup) | **Reads from `predictions.ghost_*` columns** populated by SP-1's live worker. When a ghost candle exists for the current bar, L8 returns a LayerScore based on `ghost_close > current_close ? LONG : SHORT` with strength derived from `ghost_uncertainty`. When no ghost data: returns None. |
| 7 | L9 placeholder | **Returns None** — SP-9 will populate with FinBERT + news API ingest |
| 8 | L10 placeholder | **Returns None** — SP-4 will populate with PPO brain inference |
| 9 | Static weights | **Equal weight 1/9 per layer** per meta-plan §2.3 ("L1–L9 start equal; L10 learns the actual weights"). When a layer returns None, its weight is redistributed across active layers. |
| 10 | Trap count | **12 main traps + 5 short-only filters = 17 total filters** per MASTER_PLAN §6 |
| 11 | Trap penalty | Each fired trap multiplies FINAL_SCORE by `(1 - 0.15)` (i.e., 15% penalty per trap, compounding). 3 fired traps → final = static × 0.85³ ≈ static × 0.614 |
| 12 | Asymmetric thresholds | **Shorts require static_score ≤ -X-2 where longs require static_score ≥ X** (per CLAUDE.md rule 9 + MASTER_PLAN §6 line 230) — for tier classification only; not for L2 entry rule (which already uses asymmetric thresholds per spec §5.1) |
| 13 | Tier classification | NO_SIGNAL (`<55%`) / PAPER (`55-65%`) / SMALL (`65-75%`) / STANDARD (`75-85%`) / A+ (`85%+`) per MASTER_PLAN §5 |
| 14 | FINAL_SCORE formula | `FINAL_SCORE = STATIC_SCORE × BRAIN_ADJUST × ∏(1 - 0.15)^trap_count × news_multiplier × direction_penalty` per MASTER_PLAN §5 line 215 |
| 15 | BRAIN_ADJUST default | **1.0** (no adjustment) until SP-4 wires PPO brain inference |
| 16 | news_multiplier default | **1.0** until SP-9 wires news ingest |
| 17 | direction_penalty | `1.0` for LONG, `0.95` for SHORT (asymmetric risk per CLAUDE.md rule 9) |
| 18 | Hash chain | FINAL_SCORE + tier + trap_fires lands in `predictions.layer_scores["final"]` JSONB; existing audit chain on `predictions` table covers this |

---

## 3. Architecture

### 3.1 Module layout

```
backend/app/core/scoring/
├── __init__.py                 — registry of all 10 layers
├── types.py                    ✓ exists (LayerScore, Direction, FinalScore)
├── aggregator.py               ✓ exists — extend with FINAL_SCORE formula + tier classification
├── layer1_macro.py             ✓ exists
├── layer2_patterns.py          ✓ exists (SP-2 — 158-pattern voting)
├── layer3_momentum.py          ✓ exists
├── layer4_smc.py               NEW — Smart Money Concepts
├── layer5_volume.py            ✓ exists
├── layer6_micro.py             NEW — micro-pattern aggregator
├── layer7_xgboost.py           NEW — placeholder (returns None)
├── layer8_convlstm.py          NEW — reads ghost_* from predictions
├── layer9_news.py              NEW — placeholder (returns None)
├── layer10_brain.py            NEW — placeholder (returns None)
└── traps/
    ├── __init__.py             — registry of all 17 traps
    ├── base.py                 — TrapFire dataclass + Trap Protocol
    ├── pre_news_event.py       (#1)
    ├── liquidity_sweep.py      (#2)
    ├── parabolic_blowoff.py    (#3)
    ├── friday_weekend.py       (#4)
    ├── counter_weekly.py       (#5)
    ├── all_indicator_extreme.py (#6)
    ├── alt_btc_indecision.py   (#7)
    ├── volume_no_followthrough.py (#8)
    ├── pattern_in_pattern.py   (#9)
    ├── thin_orderbook.py       (#10)
    ├── price_extreme.py        (#11)
    ├── volatility_regime_change.py (#12)
    └── short_only/
        ├── short_squeeze_cascade.py    (#13)
        ├── funding_rate_decay.py       (#14)
        ├── borrow_rate.py              (#15)
        ├── unlimited_upside_risk.py    (#16)
        └── regulatory_short_ban.py     (#17)
```

### 3.2 Layer interfaces (existing, unchanged)

```python
@dataclass(frozen=True)
class LayerScore:
    direction: Direction      # LONG / SHORT / NEUTRAL
    strength: float           # [0, 1] — magnitude
    confidence: float         # [0, 1] — quality
    notes: str                # human-readable explanation
```

### 3.3 Trap interface (NEW)

```python
@dataclass(frozen=True)
class TrapFire:
    trap_id: str
    severity: Literal["medium", "high", "extreme"]
    side: Literal["long", "short", "both"]
    reason: str
    evidence: dict[str, Any]

class Trap(Protocol):
    trap_id: str
    severity: Literal["medium", "high", "extreme"]
    side: Literal["long", "short", "both"]

    def check(
        self,
        bars: pd.DataFrame, *,
        current_idx: int,
        layer_scores: dict[int, LayerScore | None],
        proposed_direction: Direction,
        context: TrapContext,
    ) -> TrapFire | None:
        """Return TrapFire if the trap fires AGAINST the proposed direction."""
```

`TrapContext` carries shared inputs that traps need:
- `next_news_event_minutes_until: int | None`
- `is_friday_close: bool`
- `weekly_bias: Direction`
- `btc_atr_pct: float` (BTC volatility for "BTC indecision" check)
- `funding_rate: float | None`
- `open_interest_delta_24h: float | None`
- `borrow_rate_pct: float | None`

### 3.4 Aggregator update — FINAL_SCORE formula

Existing `aggregate(scores: dict[int, LayerScore | None]) -> FinalScore` extended:

```python
def aggregate(
    layer_scores: dict[int, LayerScore | None], *,
    trap_fires: list[TrapFire] | None = None,
    brain_adjust: float = 1.0,
    news_multiplier: float = 1.0,
) -> FinalScore:
    """Apply MASTER_PLAN §5 formula."""
    # 1. Static score: weighted average of active layers (None layers excluded;
    #    weights redistributed across active layers).
    active_layers = [(i, s) for i, s in layer_scores.items() if s is not None]
    if not active_layers:
        return FinalScore(score=0.0, direction=Direction.NEUTRAL,
                          confidence=0.0, contributing_layers=[])

    # Sign convention: LONG = +strength, SHORT = -strength, NEUTRAL = 0
    weighted_sum = 0.0
    confidence_sum = 0.0
    weight_per_layer = 1.0 / len(active_layers)
    for i, s in active_layers:
        signed = (
            +s.strength if s.direction is Direction.LONG
            else -s.strength if s.direction is Direction.SHORT
            else 0.0
        )
        weighted_sum += weight_per_layer * signed * s.confidence
        confidence_sum += weight_per_layer * s.confidence

    static_score = weighted_sum  # [-1, +1]

    # 2. Apply traps
    trap_fires = trap_fires or []
    trap_count = len(trap_fires)
    trap_factor = (1.0 - 0.15) ** trap_count  # compounds per trap

    # 3. Apply brain + news
    raw_final = static_score * brain_adjust * trap_factor * news_multiplier

    # 4. Direction penalty (asymmetric risk)
    direction = (
        Direction.LONG if raw_final > 0.05
        else Direction.SHORT if raw_final < -0.05
        else Direction.NEUTRAL
    )
    direction_penalty = 1.0 if direction is Direction.LONG else 0.95
    final = raw_final * direction_penalty

    return FinalScore(
        score=final,
        direction=direction,
        confidence=confidence_sum,
        contributing_layers=[i for i, _ in active_layers],
    )
```

### 3.5 Tier classification

```python
def classify_tier(final: FinalScore) -> Literal["NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+"]:
    """Convert FINAL_SCORE to actionable tier per MASTER_PLAN §5.

    Asymmetric thresholds per CLAUDE.md rule 9: shorts require +2 layers higher
    than longs. We approximate by raising the score threshold by 0.10 for SHORT.
    """
    score = abs(final.score)  # [0, 1]
    pct = score * 100         # [0, 100]
    short_bias = 0.10 if final.direction is Direction.SHORT else 0.0

    if pct < 55 + short_bias * 100: return "NO_SIGNAL"
    if pct < 65 + short_bias * 100: return "PAPER"
    if pct < 75 + short_bias * 100: return "SMALL"
    if pct < 85 + short_bias * 100: return "STANDARD"
    return "A+"
```

---

## 4. Data model

### 4.1 No new tables

All SP-5 outputs land in existing `predictions.layer_scores` JSONB:

```json
{
  "1": {"direction": "LONG", "strength": 0.7, "confidence": 0.8, "notes": "EMA20 > EMA50 > EMA200"},
  "2": {"direction": "LONG", "strength": 0.6, "confidence": 0.65, "notes": "5 patterns fired"},
  "3": {"direction": "LONG", "strength": 0.55, "confidence": 0.7, "notes": "RSI 62, MACD bullish"},
  "4": null,
  "5": {"direction": "NEUTRAL", "strength": 0.0, "confidence": 0.5, "notes": "volume avg"},
  "6": null,
  "7": null,
  "8": null,
  "9": null,
  "10": null,
  "traps_fired": [
    {"trap_id": "friday_weekend", "severity": "high", "side": "both", "reason": "..."},
    {"trap_id": "counter_weekly", "severity": "high", "side": "long", "reason": "..."}
  ],
  "static_score": 0.45,
  "brain_adjust": 1.0,
  "trap_factor": 0.7225,
  "news_multiplier": 1.0,
  "direction_penalty": 1.0,
  "final": 0.325,
  "tier": "PAPER"
}
```

### 4.2 No migration required

`predictions.layer_scores` already exists as JSONB (or TEXT serialized JSON) from SP-0. SP-5 just adds richer payload.

---

## 5. Validation procedure

Per meta-plan §3 §179: "FINAL_SCORE matches by-hand calculation on 50 fixtures".

**Fixture format:** `tools/validation/sp5_fixtures.json`:
```json
[
  {
    "name": "all_long_no_traps",
    "layer_scores": {"1": {"d":"LONG","s":0.7,"c":0.8}, "3": {"d":"LONG","s":0.6,"c":0.7}, ...},
    "trap_fires": [],
    "brain_adjust": 1.0,
    "news_multiplier": 1.0,
    "expected_static": 0.4083,
    "expected_final": 0.4083,
    "expected_tier": "NO_SIGNAL"
  },
  ...
]
```

50 fixtures cover:
- All long, no traps
- All short, no traps
- Mixed long/short layers
- 1, 2, 3, 5 traps fired
- Tier boundaries (54.9%, 55%, 64.9%, 65%, etc.)
- Asymmetric direction penalty active (SHORT)
- Brain adjust ≠ 1.0
- News multiplier ≠ 1.0
- All layers None (NEUTRAL output)

`tools/validation/sp5_cross_check.py` runs the aggregator on each fixture and compares to `expected_*` fields. Exits 0 if 50/50 match within 0.001 absolute tolerance.

---

## 6. Sub-project sequencing

SP-5 implementation order:

- **Phase A — Worktree + scaffolding + Trap Protocol + 50-fixture file** (~5 tasks)
- **Phase B — L4 SMC layer + L6 micro-pattern layer + L8 Conv-LSTM hookup** (~6 tasks)
- **Phase C — 12 main traps** (parallel-safe; can dispatch in 2 batches of 6) (~12 tasks)
- **Phase D — 5 short-only traps** (~5 tasks)
- **Phase E — Aggregator update + tier classification + asymmetric thresholds + cross-check harness** (~5 tasks)
- **Phase F — Predictor integration + admin endpoint for trap enable/disable + ship + tag** (~5 tasks)

After SP-5 ships, the natural next sub-projects:
- **SP-6** — UI completion (renders all layer scores + trap fires + tier in the 14 sidebar panels)
- **SP-7** — Ops hardening (full backtest framework, hyperopt for layer weights)
- **SP-1.5** — L7 XGBoost (parallel ML track)
- **SP-9** — News + sentiment (FinBERT for L9)

---

## 7. Cross-cutting policy compliance

| Policy | How SP-5 satisfies it |
|---|---|
| §5.14 audit hash chain | FINAL_SCORE + trap_fires land in `predictions.layer_scores` JSONB; existing chain hashes the full payload |
| §2.6 Cloudflare Access | New admin trap-enable endpoint inherits `Depends(require_admin)` from SP-0.7 |
| Per-user (SP-0.7) | Predictions stay user-scoped; SP-5 doesn't change user_id flow |
| §2.3 layer weights | Equal weight 1/9 across active layers; L10 will learn the actual weights in SP-4 |
| Asymmetric risk (CLAUDE.md rule 9) | Direction penalty 0.95 for SHORT; tier threshold +10 percentage points for SHORT |

---

## 8. Risk + fallback plan

| Failure mode | Detection | Fallback |
|---|---|---|
| L4 SMC detection too noisy (every bar fires) | Manual inspection of L4 layer score distribution over 1 week | Tighten swing-detection prominence; reduce L4 weight |
| Trap false-positive cascade (every prediction has 5+ traps fired → final≈0) | trap_fires count distribution | Reduce per-trap penalty from 0.15 to 0.10; OR mark some traps as "informational only" (don't multiply) |
| L8 Conv-LSTM hookup fails when no checkpoint active | predictions.ghost_* columns are NULL → L8 returns None | Already handled — None layers excluded from aggregator, weights redistributed |
| Cross-check fixture drift after formula tweak | tools/validation/sp5_cross_check.py exits 1 | Update fixtures to match the new formula's outputs (and document the formula change in commit) |
| Asymmetric threshold too restrictive (no SHORT signals ever fire) | 30 days of predictions show 0 SHORT tier ≥ SMALL | Reduce SHORT bias from 0.10 to 0.05 |

**SP-5 failure does NOT brick the bot.** Existing L1+L2+L3+L5 layers continue to produce scores. Aggregator returns FinalScore with whatever layers are available.

---

## 9. Acceptance criteria

- [ ] L4 (SMC) layer implemented and produces non-trivial scores on synthetic bars
- [ ] L6 (micro pattern) layer implemented
- [ ] L7, L9, L10 placeholders in place (return None) — no errors when called
- [ ] L8 (Conv-LSTM) hookup reads `predictions.ghost_*` columns, returns layer score when ghost data present
- [ ] All 12 main traps + 5 short-only traps implemented
- [ ] Aggregator applies the full FINAL_SCORE formula
- [ ] Tier classification works (NO_SIGNAL / PAPER / SMALL / STANDARD / A+) with asymmetric SHORT bias
- [ ] `tools/validation/sp5_cross_check.py` exits 0 (50/50 fixtures match within 0.001)
- [ ] Predictor integration: every closed candle's `predictions.layer_scores` includes all 10 layer slots + traps_fired + static_score + final + tier
- [ ] Admin REST `POST /api/v1/admin/traps/{trap_id}/disable` works (admin-gated)
- [ ] No regression in existing 1150+ backend tests
- [ ] At minimum 80+ new tests (each layer + each trap + aggregator + cross-check)

---

## 10. Open questions (resolved during implementation)

| # | Question | Resolved during |
|---|---|---|
| 1 | Should L4 SMC patterns count as part of L2's 158-pattern library, or a separate layer? | Phase B — L4 is SEPARATE; L2 is the 158-pattern voting layer; L4 implements 5 SMC-specific detectors (BOS, CHoCH, OB, FVG, liquidity sweep) |
| 2 | What's the trap penalty cap? After 5+ traps, final score → 0.4-ish; OK? | Phase C — cap at 4 traps (`min(trap_count, 4)` in penalty calc) so the worst case is ~0.52 multiplier; document |
| 3 | When `BRAIN_ADJUST = 0`, should we treat it as "brain says NO_SIGNAL" or as a config error? | Phase E — `0.0 < BRAIN_ADJUST < 2.0` enforced; out-of-range raises ValueError |
| 4 | TrapContext fields like `funding_rate` need data sources — where do they come from? | Phase A — Binance has funding rate via `fapi/v1/premiumIndex`; Bybit via `derivatives/v3/public/funding/history`. Adapter additions deferred to SP-3.5 if needed; for v1, TrapContext fields can be None (those traps just don't fire) |
| 5 | Should trap enable/disable have per-symbol granularity (like pattern_enabled in SP-2)? | Phase F — yes, mirror `pattern_enabled` schema → new `trap_enabled` table in migration 0011 |

---

## 11. Implementation cost estimate

- Sub-project size: **~40 tasks across 6 phases** (L4 + L6 + 17 traps + aggregator + integration + ship)
- Wall-clock: **~3-4 weeks of subagent-driven work**
- New backend modules: `app/core/scoring/{layer4_smc,layer6_micro,layer7_xgboost,layer8_convlstm,layer9_news,layer10_brain}.py`, `app/core/scoring/traps/{base,17 trap modules}/`, `app/api/routes/admin_traps.py`
- New tests: ~80-100
- Database migrations: 1 (0011 — `trap_enabled` table)
- Frontend: deferred to SP-6 (admin trap-enable UI)

---

## 12. Reference

- Meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §2.3, §3 §179
- MASTER_PLAN: `files/MASTER_PLAN.md` §5 (FINAL_SCORE formula), §6 (12 traps), §9 (sidebar panels)
- CLAUDE.md rule 9 (asymmetric thresholds)
- SP-2 spec (L2 patterns): `docs/superpowers/specs/2026-05-05-SP-2-indicators-patterns-design.md`
- SP-1 spec (Conv-LSTM ghost): `docs/superpowers/specs/2026-05-05-SP-1-ml-data-ghost-candles-design.md`

---

**END OF SP-5 FULL SCORING + TRAPS DESIGN SPEC**
