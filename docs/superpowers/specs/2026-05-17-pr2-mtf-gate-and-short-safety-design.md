# PR2 — MTF gate enabled + SHORT-side safety flags wired

**Status**: Design draft 2026-05-17. Awaiting operator review.
**Owner**: Backend (dispatcher + scoring + config + tests).
**Parent**: [Master rollout plan — Option D, 5 PRs](2026-05-17-master-rollout-plan-option-d.md).
**Predecessor**: PR1 record-only foundation (#169, lands ~2026-05-19).
**Behavior change**: YES — this is the first PR in the 5-PR rollout that enables a behavioral gate. Default flag values flip recording-only → active. Operator gates per-environment via env vars.

---

## 1. Goal

Convert PR1's recording-only MTF infrastructure into an active dispatcher gate, plus wire three SHORT-side safety branches that ship default-OFF (operator enables per environment after staging validation).

PR2 adds:
- **MTF gate** — skip dispatch when `mtf_agreement < MTF_MIN_AGREEMENT_1H` (default 3) on LONG/SHORT signals.
- **Higher-TF veto** — skip dispatch when 1d AND 1w timeframes both vote opposite to signal direction.
- **3 SHORT-only safety branches** — funding-rate hold halving, low-MTF SL tightening, high-borrow veto. All default-OFF.
- **DispatchOutcome literals** — `blocked_mtf_low_agreement`, `blocked_mtf_higher_tf_veto`, `blocked_short_high_borrow`.
- **Per-environment flag overrides** — staging can enable SHORT safety while prod stays default-OFF initially.
- **Bench mode** — `--mtf-gate-disabled` vs `--mtf-gate-enabled` added to `backend/scripts/bench_aggregator_latency.py`.
- **MTF data persistence on `live_trades`** — the `mtf_*` columns PR1 added (currently NULL) become populated for dispatched trades on the auto path.

PR2 does **NOT** modify `final_score` math, the symmetric LONG/SHORT score formula, the direction decision band, or any of PR1's recording behavior. The gate is a pre-dispatch filter, not a score change.

---

## 2. Scope (in PR2)

| ID | Feature | What lands |
|---|---|---|
| A3 | Dispatcher MTF gate | Insert gate in `dispatcher.dispatch()` after funding check, before max-concurrent; reads `proposal.mtf_agreement`; returns `DispatchResult(outcome="blocked_mtf_low_agreement")` when below threshold |
| A3-veto | Higher-TF veto | Reads `proposal.mtf_directions`; if 1d AND 1w both opposite to direction → `blocked_mtf_higher_tf_veto` |
| F-1 | `SHORT_FUNDING_HALVE_HOLD` | Default OFF. When ON + direction=SHORT + funding > +0.05%/8h → halve max-hold timeout on the trade |
| F-2 | `SHORT_TIGHTEN_SL_LOW_MTF` | Default OFF. When ON + direction=SHORT + `mtf_agreement < 5` → tighten SL by 20% on proposal before dispatch |
| F-3 | `SHORT_VETO_HIGH_BORROW` | Default OFF. When ON + direction=SHORT + borrow_rate > 10% APR → `blocked_short_high_borrow` |
| CONFIG | Pydantic settings | 5 new env-var-bound flags + 2 threshold knobs in `app/config.py` |
| PROPOSAL | `SignalProposal` extension | Add `mtf_agreement: int \| None`, `mtf_dominant_tf: str \| None`, `mtf_directions: dict[str, int] \| None` (already on `LivePredictionOut` from PR1) |
| GLUE | `proposal_from_prediction` | Thread 3 mtf fields from `LivePredictionOut` → `SignalProposal` |
| PERSISTENCE | `payload_builders.build_live_trade_payload` | Accept + persist `mtf_*` fields on `live_trades` (columns added by PR1 are currently NULL) |
| BENCH | `bench_aggregator_latency.py` | Add `--mtf-gate-disabled` / `--mtf-gate-enabled` modes; V-7 gate uses same thresholds as PR1 |
| TELEMETRY | DispatchOutcome literals | 3 new `Literal[...]` members; observability dashboards filter on new outcomes |
| TESTS | New + updated | 6 unit tests (gate fire/pass, veto fire, 3 SHORT flag defaults + overrides), 4 integration tests (LONG pass, LONG block, SHORT veto, telegram-uniformity), bench gate |

## 3. Explicitly NOT in PR2

- 15m lane in shadow worker — PR3.
- Multi-resolution shadow / `SHADOW_NARROW_UNIVERSE` config — PR3.
- Per-candle observation / schema v2 — deferred PR5.
- IC auto-weighting / regime-conditional weights — deferred PR4.
- Outcome-adaptive cooldown — PR8.
- Dynamic position sizing — PR9.
- Self-healing supervisor / FU-1/FU-2/FU-3 remediation — PR9.
- **Changes to `final_score` math, the symmetric LONG/SHORT formula, `_SHORT_DIRECTION_PENALTY`, or `SHORT_BIAS_PP`** — these knobs stay at PR #121 symmetric values (1.0 and 0.0 respectively). Future asymmetry, if needed, lands in a separate PR.
- Telegram-approve / auto-path uniformity for FU-4/5/6 (data-integrity gaps in telegram-approve trade payloads) — but PR2 must NOT make those gaps worse; see §6.4.
- New worker tasks (MTF prewarm + ttl_refresh shipped in PR1; no new workers in PR2).
- `p_win_recalibrate_task` worker — PR5.

---

## 4. Components

### 4.1 `backend/app/config.py` — 5 new flag settings + 2 threshold knobs

```python
class Settings(BaseSettings):
    ...
    # PR2: MTF gate (active in PR2; recording-only in PR1)
    MTF_MIN_AGREEMENT_1H: int = 3
    MTF_HIGHER_TF_VETO: bool = True

    # PR2: SHORT-side safety branches (all default OFF; per-env enable)
    SHORT_FUNDING_HALVE_HOLD: bool = False
    SHORT_TIGHTEN_SL_LOW_MTF: bool = False
    SHORT_VETO_HIGH_BORROW: bool = False

    # PR2: SHORT-side thresholds (only consulted when corresponding flag is ON)
    SHORT_FUNDING_HALVE_THRESHOLD_PCT: float = 0.05  # 0.05%/8h
    SHORT_VETO_BORROW_APR_PCT: float = 10.0          # 10% APR
    SHORT_TIGHTEN_SL_MTF_CUTOFF: int = 5             # mtf_agreement < this → tighten
    SHORT_TIGHTEN_SL_PCT: float = 0.20               # tighten SL distance by 20%
```

**Bounds from operator:**
- Every flag default reproduces PR1 behavior (gate computed but not enforced; SHORT branches off). The first `MTF_MIN_AGREEMENT_1H=3` default is the only behavior-flip — operator confirms via staging soak before main merge.
- All 5 flags + 4 threshold knobs are env-var bindable for per-environment override (staging vs prod).
- No "magic constants" — every threshold lives in `config.py` with a documented purpose.

### 4.2 `backend/app/trading/execution/dispatcher.py` — gate insertion

Insertion point: between funding-block check ([dispatcher.py:403](backend/app/trading/execution/dispatcher.py#L403)) and max-concurrent check ([dispatcher.py:411](backend/app/trading/execution/dispatcher.py#L411)). The exact line numbers shift after PR1; the implementation plan re-anchors.

```python
# After funding-block check, before max-concurrent:
settings = get_settings()
gate_result = _apply_mtf_gate(proposal, settings)
if gate_result is not None:
    return gate_result  # DispatchResult(outcome=<blocked_*>)
gate_result = _apply_short_safety_gates(proposal, settings, user)
if gate_result is not None:
    return gate_result
# SHORT-side SL tightening modifies proposal in place (or returns new):
proposal = _maybe_tighten_short_sl(proposal, settings)
# fall through to existing max-concurrent + leverage + place
```

**New helpers in `dispatcher.py`:**
- `_apply_mtf_gate(proposal, settings) -> DispatchResult | None`
  - Returns `blocked_mtf_low_agreement` when `proposal.mtf_agreement is not None and proposal.mtf_agreement < settings.MTF_MIN_AGREEMENT_1H`.
  - Returns `blocked_mtf_higher_tf_veto` when `settings.MTF_HIGHER_TF_VETO=True` AND `proposal.mtf_directions` is not None AND `mtf_directions["1d"]` and `mtf_directions["1w"]` BOTH have sign opposite to `proposal.direction`.
  - Returns `None` (allow dispatch) when `proposal.mtf_agreement is None` (recording-only path or compute failed — fail-open, matches PR1's MTF None semantics).
  - Returns `None` for NEUTRAL signals (already filtered upstream — NEUTRAL never reaches dispatch).
- `_apply_short_safety_gates(proposal, settings, user) -> DispatchResult | None`
  - Only runs when `proposal.direction == "SHORT"`.
  - Returns `blocked_short_high_borrow` when `settings.SHORT_VETO_HIGH_BORROW=True` AND `_lookup_borrow_apr(proposal.symbol) > settings.SHORT_VETO_BORROW_APR_PCT`. Borrow lookup uses the same data source as the existing `borrow_cost_high` trap (`intermarket_snapshots.borrow_rate_pct` or equivalent — confirm in plan phase).
- `_maybe_tighten_short_sl(proposal, settings) -> SignalProposal`
  - When `proposal.direction == "SHORT"` AND `settings.SHORT_TIGHTEN_SL_LOW_MTF=True` AND `proposal.mtf_agreement is not None` AND `proposal.mtf_agreement < settings.SHORT_TIGHTEN_SL_MTF_CUTOFF` → returns proposal with `stop_loss` distance tightened by `settings.SHORT_TIGHTEN_SL_PCT`.
  - Otherwise returns proposal unchanged.
  - **No DispatchOutcome change** — this modifies the SL price, not the dispatch outcome.

**Hold-time halving (F-1)**: `SHORT_FUNDING_HALVE_HOLD` does NOT live in `dispatcher.py`. Hold-time is enforced after order placement, in the trade-management path. The exact module is TBD in the implementation plan — candidates: `app/trading/exit_logic.py`, `app/trading/timeout_manager.py`, or wherever the existing 4h cooldown timer fires. The PR2 plan phase must trace the timer call graph end-to-end (per `dispatcher-outbound-telegram-was-unwired` memory) before deciding the hook.

**DispatchOutcome literal extension** ([dispatcher.py:118](backend/app/trading/execution/dispatcher.py#L118)):

```python
DispatchOutcome = Literal[
    # existing:
    "emitted",
    "blocked_funding",
    "blocked_max_concurrent",
    "manual_mode",
    "place_failed",
    # PR2 additions:
    "blocked_mtf_low_agreement",
    "blocked_mtf_higher_tf_veto",
    "blocked_short_high_borrow",
]
```

### 4.3 `backend/app/trading/execution/glue.py` — thread MTF fields through

`proposal_from_prediction` adds 3 fields to the returned `SignalProposal`:

```python
def proposal_from_prediction(pred: LivePredictionOut, user: User) -> SignalProposal | None:
    if pred.direction == Direction.NEUTRAL or pred.trade_setup is None:
        return None
    return SignalProposal(
        ...,
        # PR2 additions — thread through from PR1 LivePredictionOut fields:
        mtf_agreement=pred.mtf_agreement,
        mtf_dominant_tf=pred.mtf_dominant_tf,
        mtf_directions=_parse_mtf_directions_json(pred.mtf_directions_json),
    )
```

`SignalProposal` dataclass gets 3 new Optional fields (matching PR1's `LivePredictionOut` shape — `mtf_directions_json` is stored as JSON string in DB; in-memory the dispatcher reads the parsed dict).

### 4.4 `backend/app/db/payload_builders.py` — extend `build_live_trade_payload`

`live_trades.mtf_agreement`, `mtf_dominant_tf`, `mtf_directions_json` are currently always NULL (PR1 added the columns but did NOT populate them on the trade path). PR2 populates them from `SignalProposal`:

```python
def build_live_trade_payload(
    proposal: SignalProposal, order: OrderResult, *,
    ...
) -> dict[str, Any]:
    return {
        ...existing fields...,
        "mtf_agreement": proposal.mtf_agreement,
        "mtf_dominant_tf": proposal.mtf_dominant_tf,
        "mtf_directions_json": (
            json.dumps(proposal.mtf_directions, sort_keys=True, separators=(",", ":"))
            if proposal.mtf_directions is not None else None
        ),
        # p_win, effective_score, realized_vol_20d, funding_directional_adj
        # remain NULL on live_trades in PR2 — they're recording-only on
        # predictions only. PR2 scope is MTF + SHORT safety; the other
        # 4 fields stay deferred.
    }
```

**Bounds from operator:**
- The 4 non-MTF analytics fields (`p_win`, `effective_score`, `realized_vol_20d`, `funding_directional_adj`) remain NULL on `live_trades` and `shadow_trades` in PR2. They're already populated on `predictions` from PR1. Wiring them to trade tables is out-of-scope.
- `mtf_directions_json` written with `json.dumps(..., sort_keys=True, separators=(",", ":"))` to maximize replay-identity stability across the JSONB canonicalization hole (FU-2). Even though `mtf_directions_json` is in `NON_HASHED_ALLOW_LIST` (PR1) and therefore exempt from chain hashing, the canonical write form is a defensive habit pre-FU-2.
- `payload_builders` retains the bit-identical contract — golden-dict tests get updated to include the new MTF fields when proposal carries them, but the `proposal.mtf_*=None` path must produce the SAME dict as PR1's NULL columns produced.

### 4.5 `backend/scripts/bench_aggregator_latency.py` — gate-mode benchmark

Add 2 new CLI modes:
- `--mtf-gate-disabled` — runs the dispatcher with `MTF_MIN_AGREEMENT_1H=0` (PR1 behavior, gate bypassed). Baseline.
- `--mtf-gate-enabled` — runs with `MTF_MIN_AGREEMENT_1H=3` (PR2 default). Measures the gate's added latency.

Both modes go through the full `dispatch()` path with mocked Binance order placement (the bench measures decision latency, not order roundtrip). Output JSON shape unchanged from PR1; comparison vs `--mtf-disabled` baseline checks whether the **gate check itself** (read `proposal.mtf_agreement` + 1 int comparison + optional opposite-sign check) is within budget.

**V-7 gate thresholds (unchanged from PR1):**
- `delta_p50 = p50_enabled - p50_disabled ≤ 50ms`
- `delta_p99 = p99_enabled - p99_disabled ≤ 200ms`

Expected: <1ms delta (the gate is 3 boolean checks + 1 dict lookup; trivial compared to the MTF compute itself which is in baseline).

### 4.6 Telegram-approve path — gate uniformity (R3 mitigation)

`backend/app/ops/telegram_polling.py:_place_approved_order` (or whichever function in `telegram_polling.py` calls the order-placement path on user approve) must apply the SAME gate as the auto path. Two options for the plan phase:

**Option U1 (preferred)**: Telegram-approve flow calls `dispatcher.dispatch()` with the proposal, hitting the same gate. If the gate blocks, the operator's "Approve" tap returns "Trade blocked by MTF gate" instead of placing the order. Net behavior: gate is symmetric across auto and telegram paths.

**Option U2 (fallback)**: If the telegram approve path can't reach `dispatcher.dispatch()` cleanly (e.g., because the proposal data shape diverges), duplicate the gate check inline in `telegram_polling._place_approved_order`. Less clean but functionally equivalent.

**Bound from operator:**
- The plan phase must trace the telegram-approve call graph end-to-end **before** deciding U1 vs U2 (per `dispatcher-outbound-telegram-was-unwired` memory — cost 4 unnecessary PRs in 2026-05-16 by skipping this step).
- If neither option is viable without FU-4/5/6 remediation first → file a hard-FU, surface to operator, do NOT ship PR2 with the asymmetry silently.

### 4.7 Tests

New + updated under `backend/tests/`:

| File | Coverage |
|---|---|
| `tests/trading/execution/test_dispatcher_mtf_gate.py` (NEW) | Gate fires when `mtf_agreement < 3`; passes when ≥3; `None` agreement (recording-only fallback) → passes; NEUTRAL never reaches gate (assertion test). |
| `tests/trading/execution/test_dispatcher_higher_tf_veto.py` (NEW) | 1d=opposite + 1w=opposite + LONG → veto fires; 1d=opposite + 1w=same → passes; `MTF_HIGHER_TF_VETO=False` → veto never fires regardless of TF votes. |
| `tests/trading/execution/test_dispatcher_short_flags.py` (NEW) | All 3 SHORT flags read False from default config; env var override sets them True; each flag's behavior triggers only when (a) flag ON, (b) direction=SHORT, (c) threshold met; LONG signals never trigger SHORT flags. |
| `tests/trading/execution/test_dispatcher_sl_tightening.py` (NEW) | `SHORT_TIGHTEN_SL_LOW_MTF=True` + SHORT + `mtf_agreement=4` (<5) → SL distance reduced by 20%; LONG signal with same → SL unchanged; flag OFF → SL unchanged. |
| `tests/integration/test_pr2_mtf_gate_e2e.py` (NEW) | End-to-end LONG signal with high MTF → trade dispatched (`outcome="emitted"`); LONG signal with low MTF → `outcome="blocked_mtf_low_agreement"`; SHORT signal with high borrow + `SHORT_VETO_HIGH_BORROW=True` → `outcome="blocked_short_high_borrow"`. |
| `tests/integration/test_pr2_telegram_approve_uniformity.py` (NEW) | Telegram-approve path applies same gate as auto. Low-MTF signal approved via telegram → blocked at gate, NOT placed; high-MTF signal approved → placed identically to auto path. |
| `tests/db/test_payload_builders.py` (UPDATED) | Existing golden-dict tests still pass with `mtf_*=None` (PR1 compat). New golden-dict case: proposal with `mtf_agreement=4, mtf_dominant_tf="1h", mtf_directions={...}` → `live_trades` payload includes the 3 MTF fields populated. |
| `backend/scripts/bench_aggregator_latency.py` (UPDATED) | `--mtf-gate-disabled` / `--mtf-gate-enabled` modes added; CI captures JSON for both modes plus PR1's `--mtf-disabled` / `--mtf-recording` modes. |

Each test file covers BOTH flag-OFF and flag-ON behavior. Default-flag-OFF tests prevent regression where a future PR accidentally flips a default. LONG and SHORT both tested. NEUTRAL is asserted-unreachable at the gate (NEUTRAL filters out upstream in `proposal_from_prediction`).

---

## 5. Decision points (carried forward from master plan + plan phase TBD)

| # | Question | Decision | Rationale |
|---|---|---|---|
| D1 | `MTF_MIN_AGREEMENT_1H` default value | `3` (3-of-6 majority) | 2 too permissive (1-tf-agrees, 5-noise); 4 too strict before validation evidence; 3 is the lowest that filters dominant FP mode; tunable post-launch. |
| D2 | All SHORT safety flags default | `False` | Operator enables per-env after staging validation; symmetric LONG/SHORT contract preserved at the dispatcher decision level until operator opts in. |
| D3 | `MTF_HIGHER_TF_VETO` default | `True` | The veto fires only when 1d AND 1w BOTH oppose — strong signal. Was effectively True in PR1 (gate bypassed so never fired). Same default in PR2 with the gate active is consistent. |
| D4 | Telegram-approve uniformity | U1 (route through `dispatch()`) preferred; U2 (inline duplication) fallback | Single source of gate truth. Plan phase confirms via call-graph trace. |
| D5 | Hold-time halving hook location | TBD in plan phase | Spec-level scope is "halve max-hold when funding > +0.05%/8h on SHORT"; the exact module (`exit_logic.py` vs `timeout_manager.py` vs other) requires call-graph trace per `dispatcher-outbound-telegram-was-unwired` lesson. |
| D6 | Borrow-rate source for SHORT_VETO_HIGH_BORROW | Reuse the `borrow_cost_high` trap's data source | Existing trap already reads `intermarket_snapshots.borrow_rate_pct` (or equivalent — plan phase confirms). DRY; one source of borrow data. |
| D7 | MTF persistence on `live_trades` | YES on auto path; YES on telegram-approve path (per D4 uniformity) | PR1 added the columns; PR2 makes them non-NULL. Required for post-trade analysis correlating MTF state to outcome. |
| D8 | `p_win`, `effective_score`, `realized_vol_20d`, `funding_directional_adj` on `live_trades` | STAY NULL in PR2 | PR2 scope is MTF + SHORT safety only. The 4 other analytics fields wait for a future PR that has a use case beyond recording on predictions. |

## 6. Bounds from operator (must be enforced exactly)

### 6.1 Default-OFF discipline (carries forward from PR1)
- Every new flag defaults to "PR1 behavior reproduction" — except `MTF_MIN_AGREEMENT_1H=3`, the one explicit behavior-flip.
- Per-environment override via env vars must be tested (R5 unit test): unit test sets `os.environ` then constructs `Settings()` and asserts the override sticks.
- The `MTF_MIN_AGREEMENT_1H=0` rollback path must be tested in a unit test (gate bypassed when set to 0). This is the single-env-var rollback per master plan §PR2 Rollback.

### 6.2 No score/symmetry knob changes
- `_SHORT_DIRECTION_PENALTY=1.0` ([aggregator.py:34](backend/app/core/scoring/aggregator.py#L34)) is NOT modified.
- `SHORT_BIAS_PP=0.0` ([tiers.py:34](backend/app/core/scoring/tiers.py#L34)) is NOT modified.
- The ±0.05 NEUTRAL band ([aggregator.py:82-87](backend/app/core/scoring/aggregator.py#L82-L87)) is NOT modified.
- The shadow LONG/SHORT thresholds (`shadow/engine.py:18-19`, ±0.30 symmetric since PR #121) are NOT modified.
- If PR2 implementation finds it tempting to flip any of these to "make the gate work better" → STOP, surface to operator, do not silently smuggle in score asymmetry.

### 6.3 Telegram-approve uniformity (R3)
- The gate must apply to BOTH auto and telegram-approve paths (D4).
- Plan phase traces the telegram call graph end-to-end before wiring. No "I'll figure out the wiring as I go" — that produced 4 unnecessary PRs in May 2026.
- If the telegram path can't be unified without FU-4/5/6 remediation → file hard-FU, surface to operator, do NOT ship PR2 with the asymmetry silently.

### 6.4 FU-4/5/6 hygiene
- PR2 must NOT make the telegram-approve data-integrity gaps worse:
  - `inputs_hash` continues to flow through correctly (FU-6).
  - `layer_summary` continues to flow through correctly (FU-5).
  - `user_id` fallback (FU-4) is preserved — masking-the-bug is a known issue, but PR2 does not introduce a NEW masking path.
- The plan phase audits the telegram-approve trade payload against the auto-path trade payload and asserts they only differ in `approved_via="telegram"` vs `approved_via="auto"`.

### 6.5 Latency check gate (V-7, same as PR1)
- `backend/scripts/bench_aggregator_latency.py` adds `--mtf-gate-disabled` and `--mtf-gate-enabled` modes.
- N=500 samples per mode, BTCUSDT fixture.
- Outputs JSON: `{p50_ms, p95_ms, p99_ms, n_samples, mode}`.
- **PR2 merge gate (operator review)**: `delta_p50 ≤ 50ms` AND `delta_p99 ≤ 200ms`. Same budgets as PR1.
- Expected `delta_p50` is sub-1ms; the gate is 3 boolean checks. Failure would indicate a regression elsewhere.
- If either gate fails → STOP, surface to operator, redesign before merge.

### 6.6 Audit chain hygiene
- The 3 MTF fields persisted on `live_trades` (`mtf_agreement`, `mtf_dominant_tf`, `mtf_directions_json`) stay in `NON_HASHED_ALLOW_LIST` — already done in PR1.
- No new columns added to `HASH_PAYLOAD_COLUMNS` in PR2.
- Existing `test_audit_replay_identity.py` must still pass on the post-PR2 schema.

### 6.7 Hard out-of-scope (do NOT add in PR2)
- 15m lane (PR3).
- Multi-resolution shadow (PR3).
- Outcome-adaptive cooldown (PR8).
- Dynamic sizing (PR9).
- Self-healing supervisor (PR9).
- Changes to `final_score` math or any score-formula knob.
- New analytics fields on `live_trades` beyond the 3 MTF fields.
- New worker tasks.
- p_win model fitting (PR5).
- Frontend changes (PR2 is pure backend; the new DispatchOutcome literals surface in admin dashboards but no frontend code ships).

---

## 7. Risks + mitigations

| Risk | Mitigation |
|---|---|
| **R1** Threshold too strict → signal rate drops > 40%, win rate flat | Lower `MTF_MIN_AGREEMENT_1H` to 2 via env var; investigate why MTF dominant TFs aren't agreeing |
| **R2** Threshold too permissive → signal rate ~same, win rate flat | Raise `MTF_MIN_AGREEMENT_1H` to 4 via env var |
| **R3** Telegram-approve path skips gate → auto/telegram asymmetry | §4.6 + §6.3 — gate explicitly applied in both paths; plan phase traces call graph end-to-end |
| **R4** Latency regression on gate enable (gate computation runs on every signal, not just decisions) | V-7 bench measures `--mtf-gate-enabled` vs `--mtf-gate-disabled`; fail PR2 if budget exceeded |
| **R5** SHORT safety flags default-ON by accident in some env → unexpected hold-time halving on shorts | Unit test asserts all SHORT flags read `False` from default `Settings()`; integration test that env var override works correctly |
| **R6** PR1 MTF compute returns `None` (TF fetch failures) → gate decision based on stale state | Fail-open: `proposal.mtf_agreement is None` → gate PASSES (matches PR1's "no signal poisoning" contract). The trade may dispatch on cold-cache; PR1 already accepts this latency tradeoff. |
| **R7** SHORT_VETO_HIGH_BORROW relies on borrow-rate freshness (intermarket_snapshots can be stale) | Plan phase confirms borrow-rate staleness budget; if `intermarket_snapshots` row is older than e.g. 6h, treat as "borrow data unavailable" and fail-open (don't veto on stale data). Document the chosen staleness budget. |
| **R8** MTF persistence on `live_trades` causes a write-time race when proposal arrives without `mtf_*` (rare but possible if MTF cache is cold during dispatch) | Payload builder accepts `None` cleanly; columns are nullable per PR1 schema. No write-time error possible. |

## 8. Rollback

**Two-stage rollback** (graceful → forceful):

**Stage 1 — gate bypass (no DB change, no code revert)**:
- Set `MTF_MIN_AGREEMENT_1H=0` in env. Gate computes but always passes (0 ≤ any agreement).
- Set all SHORT_* flags to `False` (the default). SHORT safety branches dormant.
- Net effect: PR2 behavior reverts to PR1 recording-only. Trades dispatch identically to pre-PR2.
- **No DB rollback needed** — PR2 adds no schema. The `live_trades.mtf_*` columns continue to be written (now populated; was NULL in PR1) but no read path consumes them as gates.

**Stage 2 — full PR revert** (if Stage 1 insufficient):
- Standard `git revert <merge-commit>` reverses dispatcher + glue + payload-builder changes.
- `live_trades.mtf_*` writes revert to NULL on the auto path.
- No alembic downgrade needed.

## 9. Exit criteria (PR2 ships when)

1. ✅ All CI green (backend, frontend, docker-smoke).
2. ✅ mypy clean (404+ source files).
3. ✅ All 4 new unit test files + 2 new integration test files pass.
4. ✅ `test_audit_replay_identity.py` (from PR1) still passes — no regression in hash-chain replay.
5. ✅ Latency check gate passed — `bench_aggregator_latency.py --mtf-gate-enabled` shows `delta_p50 ≤ 50ms` and `delta_p99 ≤ 200ms` vs `--mtf-gate-disabled` baseline.
6. ✅ Telegram-approve uniformity test passes — the gate fires identically on auto and telegram-approve paths.
7. ✅ Spec compliance reviewer: PASS on all bounds (§6).
8. ✅ Code quality reviewer: 0 Critical findings.
9. ✅ Manual operator review of the full diff.
10. ✅ **5+ day staging soak** with `MTF_MIN_AGREEMENT_1H=3` enabled on staging.
11. ✅ **Shadow stats during soak**: signal rate drops 30-40%, win rate flat or improving, zero new `auth_violations` or audit chain breaks.
12. ✅ Operator free-text "ship it" (or equivalent) for dev → main merge (per `dev-prod-branch-workflow` memory).

## 10. References

- Parent: [master rollout plan — Option D](2026-05-17-master-rollout-plan-option-d.md)
- Predecessor spec: [PR1 record-only foundation](2026-05-16-pr1-record-only-design.md)
- Predecessor plan: [PR1 implementation plan](../plans/2026-05-16-pr1-record-only.md)
- KNOWN_ISSUES: `backend/docs/KNOWN_ISSUES.md` (FU-1 through FU-10; FU-4/5/6 most relevant to PR2 telegram uniformity)
- Architecture: `docs/ARCHITECTURE.md`
- MEMORY entries: `dispatcher-outbound-telegram-was-unwired`, `dev-prod-branch-workflow`, `complete-modules-before-merge`, `shadow-entry-thresholds`
- Hook points (PR1 code post-merge):
  - [`backend/app/trading/execution/dispatcher.py:118`](backend/app/trading/execution/dispatcher.py#L118) — DispatchOutcome literal extension
  - [`backend/app/trading/execution/dispatcher.py:408`](backend/app/trading/execution/dispatcher.py#L408) — gate insertion point (after funding, before max-concurrent)
  - [`backend/app/core/scoring/aggregator.py:34`](backend/app/core/scoring/aggregator.py#L34) — `_SHORT_DIRECTION_PENALTY=1.0` (DO NOT MODIFY in PR2)
  - [`backend/app/core/scoring/aggregator.py:82-87`](backend/app/core/scoring/aggregator.py#L82-L87) — direction decision (read-only)
  - [`backend/app/core/scoring/tiers.py:34`](backend/app/core/scoring/tiers.py#L34) — `SHORT_BIAS_PP=0.0` (DO NOT MODIFY in PR2)
  - [`backend/app/ops/telegram_polling.py`](backend/app/ops/telegram_polling.py) — telegram-approve path (call-graph trace required in plan phase)
  - [`backend/app/db/payload_builders.py`](backend/app/db/payload_builders.py) — extend `build_live_trade_payload`
  - [`backend/scripts/bench_aggregator_latency.py`](backend/scripts/bench_aggregator_latency.py) — add 2 new modes
- Existing SHORT-side traps (must not be broken or duplicated by PR2 SHORT flags): `oi_squeeze`, `funding_squeeze`, `borrow_cost_high`, `ath_proximity` (see SP-5 plan).
