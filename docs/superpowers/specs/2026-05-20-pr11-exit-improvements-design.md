# PR11 — Exit improvements (T1.3 + T1.4) design

**Status:** spec drafted overnight 2026-05-20 per operator's standing PR11 directive. Awaits operator morning review per "stand-down breakpoint" instruction.

**Scope (per operator):** T1.3 = TIMEOUT_BARS per-TF made operator-tunable. T1.4 = enforce TP ≥ 2× SL distance invariant at signal generation.

**Branch:** `feat/pr11-spec-exit-improvements` (this spec only). Implementation will land on `feat/pr11-impl-exit-improvements` after operator approves.

**Strategic context:** Bot is in `mode=manual`; these are shadow-trading-quality improvements (winners/losers split visible per PR10 + PR10.5 cards). Operator's PR sequence has PR11 queued before PR12+. No real-money risk.

---

## 1. Current state (verified by code read)

### T1.3 — Per-TF timeout location

`backend/app/shadow/exit_monitor.py:11`:

```python
# PR3 Phase 5 (spec §4.6): per-TF exit timeout. Equal ~24h wall-clock
# holdtime across TFs: 1h → 24 bars, 15m → 96 bars. KeyError on unknown
# TF is a programming-error fail-loud — new TFs require an explicit
# entry here AND a matching entry in HOLD_TP_SCALING_TABLE for G1.
TIMEOUT_BARS_PER_TF: dict[str, int] = {
    "1h": 24,
    "15m": 96,
}
```

This is a **module-level constant**, not a Settings field. Operator cannot tune per environment without editing code + redeploying. Adding a new TF requires:
1. Editing this dict
2. Editing `HOLD_TP_SCALING_TABLE` (PR3 G1)
3. Editing `SHADOW_COOLDOWN_HOURS` (already in Settings)

Settings already has `SHADOW_TIMEFRAMES: list[str] = ["1h", "15m"]` and `SHADOW_COOLDOWN_HOURS: dict[str, float]`, so the pattern for "operator-tunable per-TF dict" is established.

### T1.4 — TP/SL ratio computation

`backend/app/shadow/engine.py:23-24`:

```python
SL_ATR_MULT: float = 1.5
TP_ATR_MULT: float = 3.0
```

Used in `ShadowEngine.generate_signal`:

```python
sl = last_close - self.sl_atr_mult * atr   # LONG SL below entry
tp = last_close + self.tp_atr_mult * atr   # LONG TP above entry
```

(SHORT inverts signs identically.)

Current default ratio: `3.0 / 1.5 = 2.0` — already meets the "TP ≥ 2× SL" target. **But:**
- The values are also module-level constants, not Settings.
- No invariant enforcement — if `sl_atr_mult` and `tp_atr_mult` are ever overridden such that `tp_mult / sl_mult < 2.0`, the engine accepts and emits signals anyway.
- PR3 G1 `HOLD_TP_SCALING_TABLE` can multiply TP further (worker.py:587), but never reduces it — so G1 only increases ratio, never decreases. Safe.
- No telemetry surfaces the effective ratio on emitted signals — operator can't audit live.

---

## 2. T1.3 design: per-TF timeout in Settings

### Spec

Move `TIMEOUT_BARS_PER_TF` from `app/shadow/exit_monitor.py:11` to a new `Settings` field:

```python
# --- PR11 T1.3: per-TF exit timeout (operator-tunable) ---------------
# Equal ~24h wall-clock holdtime across TFs in default. Operator tunes
# per environment when shadow stats reveal that a given TF's median
# winning trade hits TP well before the 24h ceiling (room to extend)
# or well after (room to shorten without missing fills).
SHADOW_TIMEOUT_BARS: dict[str, int] = {"1h": 24, "15m": 96}
```

Compatibility shim:
- The existing module-level `TIMEOUT_BARS_PER_TF` and `TIMEOUT_BARS` symbols stay exported from `app/shadow/exit_monitor.py` for backwards compatibility, BUT their values are now read from `Settings.SHADOW_TIMEOUT_BARS` lazily at first access.
- Callers like `check_exit` resolve the limit via the Settings-backed dict, not the module-level constant.

### Behavior contract

- Default values: `{"1h": 24, "15m": 96}` — bit-identical to current behavior on a default config.
- New TFs added by operator to `SHADOW_TIMEFRAMES` must ALSO have a matching key in `SHADOW_TIMEOUT_BARS` and `SHADOW_COOLDOWN_HOURS`. `check_exit` continues to raise `KeyError` on missing TF (fail-loud programming-error). A startup-validation function asserts the three dicts share keys.
- `HOLD_TP_SCALING_TABLE` (PR3 G1) is not modified — when G1 is OFF (default), the per-TF baseline is used directly. When ON, the table multiplier applies to the per-TF baseline, unchanged.

### Tests

- Unit (1 new): asserts default `SHADOW_TIMEOUT_BARS == {"1h": 24, "15m": 96}` (matches pre-PR11 constant).
- Unit (1 new): `check_exit` reads from Settings — patch `get_settings()` to return a `SimpleNamespace(SHADOW_TIMEOUT_BARS={"1h": 5, ...})`, assert a 6-bar-old position TIMEOUT-exits.
- Unit (1 new): startup-validation function flags missing key when `SHADOW_TIMEFRAMES` has a TF not in `SHADOW_TIMEOUT_BARS`. This wires from main.py's startup path; runs at server boot.
- Regression: existing `tests/unit/test_shadow_exit_monitor.py` (or equivalent — read repo) must still pass.

---

## 3. T1.4 design: enforce TP ≥ 2× SL invariant

### Spec

Add a new `Settings` field:

```python
# --- PR11 T1.4: minimum TP-to-SL distance ratio ----------------------
# Operator's "winning trades need 2-to-1 reward-to-risk minimum" rule.
# Signals with effective_tp_distance / effective_sl_distance below this
# value are rejected at engine.generate_signal — recorded to a counter
# (PR10.5 `/per-asset` stats card surfaces the rejection rate if added,
# but T1.4 itself does not surface a UI; that's an optional PR12 hook).
SHADOW_MIN_TP_SL_RATIO: float = 2.0
```

In `engine.generate_signal`, after computing `sl` and `tp`:

```python
sl_dist = abs(last_close - sl)
tp_dist = abs(tp - last_close)
if sl_dist <= 0 or (tp_dist / sl_dist) < settings.SHADOW_MIN_TP_SL_RATIO:
    # Reject — log + metric increment
    log.info(
        "shadow_engine: signal rejected by TP/SL ratio rule — "
        "symbol=%s direction=%s tp_dist=%.6f sl_dist=%.6f ratio=%.3f cutoff=%.2f",
        symbol, "LONG" if score > 0 else "SHORT",
        tp_dist, sl_dist, tp_dist / sl_dist if sl_dist > 0 else 0.0,
        settings.SHADOW_MIN_TP_SL_RATIO,
    )
    return None
```

### Behavior contract

- Default value: `2.0` — matches current `TP_ATR_MULT / SL_ATR_MULT = 3.0 / 1.5 = 2.0`. So on a default config, **zero signals are rejected** that weren't already rejected.
- Tunable per env. Setting to `0.0` is the single-flag rollback (rule passes for all ratios).
- The check is post-SL/post-TP computation — so any environment-specific override of `SL_ATR_MULT` / `TP_ATR_MULT` is correctly enforced.
- The rule applies **only at signal-emit time**. Once a position is open, no mid-trade recomputation. (PR3 G1 may grow TP further after open, never shrink — so the invariant is always at-least preserved for the lifetime of the position.)
- Telemetry: log INFO (not WARNING) — rejections are expected, not anomalous. Counter accumulation deferred to a future PR.

### Tests

- Unit (1 new): with default ratio (2.0), a `ShadowEngine` configured with `sl_atr_mult=1.0, tp_atr_mult=2.0` — produces signal (ratio == 2.0 passes `<` check, not `<=`).
- Unit (1 new): with default ratio (2.0), `tp_atr_mult=1.5` — signal rejected (ratio 1.5 < 2.0).
- Unit (1 new): with ratio set to 0.0 (rollback), `tp_atr_mult=0.1, sl_atr_mult=1.0` — signal passes (zero-cutoff bypass).
- Regression: existing engine tests still pass with default config.

---

## 4. Audit chain impact

**None.** No new tables. No persisted state changes.

---

## 5. V-7 latency budget

- T1.3: one extra `Settings` dict lookup per `check_exit` call. Sub-microsecond. Δ0.
- T1.4: 2 float subtractions + 1 division + 1 comparison per signal candidate. Sub-microsecond. Δ0.

Dispatcher hot path unchanged.

---

## 6. Rollback

- T1.3: revert PR. Module constant restored. Operator-set Settings overrides lost.
- T1.4: env `SHADOW_MIN_TP_SL_RATIO=0.0` disables the rule entirely. Restart needed (`lru_cache`).

Single revert: `git revert <PR11-squash-sha>` reverses both.

---

## 7. Out of scope (deferred)

- Counter accumulation for T1.4 rejection rate (UI surfacing) — would need a new persisted counter or in-memory module state. Trivial follow-up if operator wants visibility.
- Adaptive TP/SL ratio per-symbol or per-mtf_agreement — out of scope until shadow stats accrue enough evidence.
- TP scaling based on momentum at signal time — out of scope; existing PR3 G1 already provides one knob.

---

## 8. Decision points for operator morning review

| # | Decision | Default proposed | Alternative |
|---|---|---|---|
| 1 | T1.3 Settings name | `SHADOW_TIMEOUT_BARS` | `SHADOW_TIMEOUT_BARS_PER_TF` (more explicit) |
| 2 | T1.4 cutoff default | `2.0` | `0.0` ship-disabled, opt-in |
| 3 | Rejection log level | `INFO` | `DEBUG` (quieter) or `WARNING` (louder) |
| 4 | Startup-validation strictness | Raise on missing TF key | Log WARNING + fall back to legacy constant |
| 5 | T1.3 module-constant compat shim | Keep export, read from Settings | Drop module constant entirely (caller migration) |
| 6 | Combined PR or split into PR11a + PR11b | Combined (small surface) | Split (decision-by-decision granularity) |
| 7 | Backfill historical signals with retroactive ratio rejection | No — historical shadow_trades unchanged | Yes via separate one-shot recompute |

---

## 9. Test plan summary

| Layer | Tests | Description |
|---|---|---|
| Unit | 6 new | 3 T1.3 (default, override, validate), 3 T1.4 (pass-at-boundary, reject-below, rollback-zero) |
| Regression | existing | shadow_engine + exit_monitor suites still pass |
| Integration | optional | endpoint smoke for the rejected-signal log not strictly needed |
| Lint/mypy | clean | new fields are typed `dict[str, int]` + `float` |
| V-7 bench | N/A | dispatcher hot path unchanged |

---

## 10. Operator surface

This is the **stand-down breakpoint** per operator's overnight directive: spec doc review is the natural breakpoint where operator input adds value. Implementation begins only after morning approval.

Operator should confirm:
1. The 7 decision-point defaults (or override).
2. The scope (T1.3 + T1.4 only — confirm no PR11.5 scope creep needed).
3. Whether to combine into one PR or split (decision #6).
