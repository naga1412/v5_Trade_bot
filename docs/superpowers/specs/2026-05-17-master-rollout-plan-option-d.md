# v5 Trade Bot — Master Rollout Plan (Option D, locked 2026-05-17)

Compressed 5-PR rollout. RL/brain work (originally PR4-PR7)
deferred to v2 evaluation queue after operator pace decision
2026-05-17.

## Goal

Ship the deterministic-improvement subset of the original 12-feature
upgrade in ~5 weeks, deferring speculative RL/brain work for v2
decision after 2+ months of validation data on the deterministic
improvements.

## The 5 PRs

| PR | Title | Behavior change? | Target prod |
|----|-------|------------------|-------------|
| 1  | Record-only foundation (MTF/p_win/vol-norm/funding analytics + audit chain hardening) | No | Tue 2026-05-19 |
| 2  | MTF gate enabled + SHORT-side safety flags wired | YES — first behavior change | ~Wed 2026-05-27 |
| 3  | Multi-resolution shadow (15m lane + prewarm + narrow universe) | YES — 4× signal rate | ~Wed 2026-06-03 |
| 8  | Outcome-adaptive cooldown | YES — replaces fixed 4h cooldown | ~Wed 2026-06-10 |
| 9  | Dynamic position sizing ($10→∞) + true self-healing supervisor | YES — biggest, live money | ~Mon 2026-06-22 |

## Deferred to v2 evaluation queue (NOT shipping in Option D)

| PR | Title | Why deferred |
|----|-------|--------------|
| 4  | IC auto-weighting + regime-conditional weights | Needs 30+ days of MTF shadow data; reassess after PR3 ships |
| 5  | Per-candle obs + BC pretrain + reward shaping | RL brain currently a placeholder — deferred until rule-based system is validated as the baseline |
| 6  | Train v2 brain | Depends on PR5 |
| 7  | RL ensemble + distillation | Depends on PR6 |

v2 decision point: ~September 2026 after Option D fully validated
in prod for 2+ months. Operator reassesses with real data.

## PR2 — MTF gate enabled + SHORT-side safety flags wired

### Goal
Convert PR1's recording-only MTF infrastructure into an active
dispatcher gate. Add SHORT-side safety branches wired but
default-OFF (operator enables per environment after staging
validation).

### Scope IN
- Dispatcher MTF gate: skip dispatch when mtf_agreement < threshold
- 1d+1w veto: skip when both higher TFs vote opposite to signal
- SHORT safety branches (default OFF, wired for per-env enable):
  - SHORT_FUNDING_HALVE_HOLD: halve hold time when funding > +0.05%/8h
  - SHORT_TIGHTEN_SL_LOW_MTF: tighten SL 20% when mtf_agreement < 5
  - SHORT_VETO_HIGH_BORROW: veto if borrow rate > 10% APR
- DispatchOutcome literals: "blocked_mtf_low_agreement",
  "blocked_mtf_higher_tf_veto", "blocked_short_high_borrow"
- Per-environment flag override (staging can enable SHORT
  safety while prod stays default-OFF initially)
- Bench: --mtf-gate-disabled vs --mtf-gate-enabled modes added
  to backend/scripts/bench_aggregator_latency.py
- V-7 gate: delta_p50 ≤ 50ms, delta_p99 ≤ 200ms (same as PR1)

### Scope OUT
- 15m lane (PR3)
- Multi-resolution shadow (PR3)
- Per-candle obs (deferred PR5)
- Anything in PR3+
- Telegram-approve uniformity with auto path (FU-4/5/6 cleanup
  is its own concern, not blocking PR2 — but PR2 must NOT make
  the gap worse)

### Default flag values
- MTF_MIN_AGREEMENT_1H: 3 (was 0 in PR1 = bypass)
- MTF_HIGHER_TF_VETO: True (was True in PR1, no-op since gate was off)
- SHORT_FUNDING_HALVE_HOLD: False
- SHORT_TIGHTEN_SL_LOW_MTF: False
- SHORT_VETO_HIGH_BORROW: False

Per-env overrides via env vars allowed for all of the above.

### Why threshold = 3 (not 4, not 2)
- 3-of-6 = majority of timeframes agree with signal direction
- 2-of-6 = weak signal, too permissive (might let through noise)
- 4-of-6 = strict, would drop signal volume too aggressively
  before we have evidence that strictness pays off
- 3 is the lowest threshold that prevents "1 timeframe agrees,
  5 are noise" false positives — the dominant failure mode we're
  targeting
- Tunable post-launch based on shadow stats; start permissive,
  tighten if false positive rate stays elevated

### Expected shadow stats after enabling (validation criteria)
- Signal rate drops 30-40% (false positives filtered)
- Win rate improves 5-10 percentage points
- Sharpe stable or improved
- No regression in dispatch latency (V-7 gate)
- Zero new auth_violations or audit chain breaks

### Rollback
Single env var: `MTF_MIN_AGREEMENT_1H=0` → gate bypassed,
reverts to PR1 recording-only behavior. No DB rollback needed
(no schema change in PR2).

### Risk list — first behavior-change PR
- R1: Threshold too strict → signal rate drops > 40%, win rate
  flat. Mitigation: lower threshold to 2 via env var; investigate
  why MTF dominant TFs aren't agreeing.
- R2: Threshold too permissive → signal rate ~same, win rate flat.
  Mitigation: raise threshold to 4 via env var.
- R3: Telegram-approve path skips gate (FU-4/5/6 gap) →
  asymmetric behavior between auto and telegram. Mitigation: PR2
  explicitly applies the gate in BOTH dispatcher paths (auto
  + telegram_polling._place_approved_order). If telegram path
  can't reach mtf data, file a hard-FU and operator decides
  whether to delay PR2 or ship with the asymmetry.
- R4: Latency regression on gate enable (gate computation runs
  on every signal, not just decisions). Mitigation: V-7 bench
  measures gate-enabled vs gate-disabled; fail PR2 if budget
  exceeded.
- R5: SHORT safety flags default-on by accident in some env
  → unexpected hold-time halving on shorts. Mitigation:
  explicit unit test that all SHORT flags read False from
  default config + integration test that env var override
  works correctly.

### Hook points (from PR1 code post-merge)
- Dispatcher gate insertion: `backend/app/core/dispatcher.py:408`
  (after funding check, before max-concurrent)
- Add DispatchOutcome literal at `dispatcher.py:118`
- Direction decision (read-only for PR2):
  `backend/app/core/scoring/aggregator.py:82-87`
- Symmetry knobs (DO NOT modify in PR2):
  `tiers.py:34 SHORT_BIAS_PP=0.0`,
  `aggregator.py:34 _SHORT_DIRECTION_PENALTY=1.0`
- Existing SHORT traps already in stack: oi_squeeze,
  funding_squeeze, borrow_cost_high, ath_proximity. SHORT_*
  flags must layer ON TOP of these, not replace them.

### Test plan
- Unit: dispatcher gate fires when mtf_agreement < threshold
- Unit: dispatcher gate passes when mtf_agreement >= threshold
- Unit: 1d+1w veto fires when both vote opposite
- Unit: SHORT_* flags read False from default config
- Unit: SHORT_* flags respect env var override
- Integration: end-to-end LONG signal with high MTF agreement
  → trade dispatched
- Integration: end-to-end LONG signal with low MTF agreement
  → trade blocked, DispatchOutcome="blocked_mtf_low_agreement"
- Integration: SHORT_VETO_HIGH_BORROW=true + borrow rate 12% APR
  + SHORT signal → trade blocked
- Integration: telegram-approve path applies same gate as auto
  (uniformity test)
- Bench: V-7 gate runs and reports delta_p50/p99

### Acceptance criteria (must all pass)
- All unit + integration tests green
- V-7 bench passes (delta_p50 ≤ 50ms, delta_p99 ≤ 200ms)
- Spec compliance reviewer: PASS on all bounds
- Code quality reviewer: 0 Critical findings
- Manual operator review of full diff
- 5+ day staging soak with MTF_MIN_AGREEMENT_1H=3 enabled
- Shadow stats during soak: signal rate drops 30-40%, win rate
  flat or improving, zero new audit chain breaks

## PR3 — Multi-resolution shadow
(Scope/details to be expanded into its own spec after PR2 lands.
 High-level: enable 15m lane in shadow worker, add prewarm,
 support narrow universe via SHADOW_NARROW_UNIVERSE config.
 4× signal rate accelerates promotion gate fills.)

## PR8 — Outcome-adaptive cooldown
(Scope/details: spec `2026-05-18-pr8-outcome-adaptive-cooldown-design.md`;
 plan `2026-05-18-pr8-outcome-adaptive-cooldown.md`.
 Scope corrected on draft: surface scan revealed there is NO live
 cooldown today (`DispatchOutcome.blocked_cooldown` exists but is
 never wired; `live_trades.exit_reason` is never populated). PR8
 therefore lands three intertwined deliverables:
 (1) `live_exit_monitor` worker that classifies TP/SL/TIMEOUT/
 EXTERNAL_CLOSE + writes `live_trades.exit_reason` + upserts
 `live_cooldowns`; liquidation_monitor writes the
 `liquidation_buffer_breach` path.
 (2) `live_cooldowns` table + `_apply_cooldown_gate` in dispatcher
 pre-conditions (between funding and MTF).
 (3) Outcome-adaptive durations: SL=8h+fresh-MTF-required, TP=1h,
 TIMEOUT=4h, MANUAL/EXTERNAL=0h, LIQ_BUFFER=24h.
 Wave-regime detection doesn't exist in live code paths — PR8 adds
 `LIVE_COOLDOWN_REGIME_AWARE` flag as forward-compat hook but no
 behavior; detector defers to a future PR. Default-OFF in prod via
 `LIVE_COOLDOWN_ENABLED=False`.)

## PR9 — Dynamic sizing + Telegram alert routing (scope-trimmed self-healing)
(Scope/details: spec `2026-05-18-pr9-dynamic-sizing-self-healing-design.md`;
 plan `2026-05-18-pr9-dynamic-sizing-self-healing.md`.
 Scope corrected on draft: surface scan revealed (a) `p_win` is async
 and doesn't return yet — PR9 uses `confidence_pct/100` proxy with no-op
 forward hook for PR5; (b) multi-entry split + balance tiers are
 greenfield; (c) stateful-worker auto-restart needs in-memory state
 migration design, carved out as **FU-21** in KNOWN_ISSUES.md; (d) FU-2
 + FU-3 are independent investigations, not load-bearing.
 PR9 ships:
 (1) Kelly-fractional sizing × balance tier × hard caps (small=1%,
     medium=2%, large=5%, whale=10% of bankroll).
 (2) Multi-entry split (DCA-style) for sub-threshold confidence.
 (3) Telegram alert routing for stateful-worker critical alerts.
 Live-money exposure — **operator carve-out**: 7-day staging soak +
 explicit operator "ship it" before dev→main. Default-OFF in prod via
 `DYNAMIC_SIZING_ENABLED=False`.)

## Quality gates (apply to every PR)
- TDD: failing tests first
- Spec doc → operator review → implementation plan → operator
  review → per-task implementation → reviewers → operator
  review → merge
- Two operator merge gates per PR (feat→dev, dev→main)
- dev→main requires free-text "ship it" (not just AskUserQuestion)
- No --no-verify, no skip-hooks, no auto-merge
- 24-48h staging soak for recording-only PRs
- 5+ day staging soak for behavior-changing PRs
- 7+ day staging soak for PR9 (highest risk — live money sizing)
- Audit chain replay-identity verification post-deploy
- V-7 latency gate on any PR that touches the predictor hot path
- Default-OFF flags for any new behavior; operator flips per env

## Open FU items (orthogonal to rollout)
See backend/docs/KNOWN_ISSUES.md for the authoritative list:
- FU-1: Wire heartbeats for 12 currently-blind workers
- FU-2: Audit chain v2 (JSONB canonical hashing + alert routing
  + CHAINED_TABLES expansion to all 7 tables)
- FU-3: Investigate 5-each-in-8-seconds auth_violations pattern
- FU-4/5/6: Telegram-approve path data integrity gaps
- FU-7: Pre-existing SQLite test failures
- FU-8: CLOSED by PR #169
- FU-9: Project-wide httpx.AsyncClient hygiene (~20 modules)
- FU-10: Migration downgrade path untested

---

## Strategic replan addendum (2026-05-19)

After Option D rollout completion, operator's reassessment of shadow
stats surfaced bimodal symbol performance + missing real-money gates.
Defer real-money trading until shadow Sharpe > 0.5 (2-week window).
New PR sequence:

| PR | Title | Status |
|----|-------|--------|
| 10 | Symbol allowlist + stablecoin filter | spec 2026-05-19; impl in progress |
| 11 | Exit improvements (timeout scaled, TP ≥ 2× SL) | queued |
| 12 | Spread + liquidity filter | queued |
| 13 | Bug bundle: FU-26 + FU-27 | queued |
| 14 | Trailing stops + partial profit | queued |
| 15 | FU-24 audit chain advisory lock | queued |
| 16+ | Tier 2 batches | queued |

Real-money fully-auto re-attempt: NOT before PR13 ships AND operator
fixes Binance Futures-Trade permission (separate operator-side work).
