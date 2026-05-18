# PR9 — Dynamic sizing + Telegram alert routing (scope-trimmed self-healing)

**Status**: Design draft 2026-05-18. Awaiting operator review.
**Owner**: Backend (sizing + dispatcher + alembic + alerts).
**Parent**: [Master rollout plan — Option D, 5 PRs](2026-05-17-master-rollout-plan-option-d.md).
**Predecessor**: PR8 outcome-adaptive cooldown (in flight 2026-05-18).
**Behavior change**: YES — adds Kelly-fractional sizing scaled by confidence + balance tier. Default-OFF in prod via `DYNAMIC_SIZING_ENABLED=False`. Live-money exposure — operator carve-out applies: dev→main requires explicit "ship it".

---

## 1. Goal

The master rollout doc describes PR9 as:

> *"Kelly-fractional sizing tied to p_win + balance tier; multi-entry split for sub-100% confidence; self-healing supervisor handles all FU-1 + FU-2 + FU-3 concerns plus auto-restart workers without operator paging."*

A surface scan reveals scope tension:
- **`p_win` doesn't exist** — `app/core/scoring/p_win_calibrator.py:predict_p_win` always returns `None`. PR5 (deferred) will wire isotonic regression once shadow_trades has enough outcome data. **PR9 uses `confidence_pct` (0-100) as a p_win proxy** with a `SIZING_USE_P_WIN_WHEN_AVAILABLE=True` forward-compat hook for the eventual swap.
- **No multi-entry split exists** — greenfield. PR9 adds it.
- **No balance tiers** — greenfield. PR9 adds 4 buckets + schema.
- **Self-healing supervisor exists** for stateless workers (`worker_supervisor.py`); stateful workers are alert-only. Auto-restarting stateful workers needs in-memory state migration — that's a separate design effort. **PR9 closes the cheaper half**: wire **Telegram alert routing** so stateful-worker alerts go to the operator's phone instead of falling back to logs when SMTP isn't configured.
- **FU-2 (audit chain v2)** and **FU-3 (verifier stability)** are independent investigations queued in `KNOWN_ISSUES.md`. They're not load-bearing for PR9's sizing/alerts and stay deferred. The master rollout doc's "absorb FU-1+FU-2+FU-3" wording is aspirational — only FU-1 was closeable in a single PR (PR3 closed it).

**PR9 therefore ships:**
1. Kelly-fractional sizing scaled by `confidence_pct` × balance tier × hard caps.
2. Multi-entry split (DCA-style) when `confidence_pct < SIZING_MULTI_ENTRY_THRESHOLD`.
3. Balance tiers (4 buckets) + per-tier Kelly fraction caps.
4. Telegram alert routing for stateful-worker alerts (replaces SMTP fallback).
5. 7-day staging soak before operator approves dev→main (carve-out per master doc).

**PR9 does NOT change:**
- Predictor scoring math, MTF compute, trap layers, p_win calibrator (still stub).
- PR2's MTF gate, PR3's shadow worker, PR8's cooldown gate.
- The dispatcher's pre-conditions block ordering (sizing runs AFTER all gates).
- Tax events, audit chain v2, verifier semantics.

---

## 2. Scope (in PR9)

| ID | Feature | What lands |
|---|---|---|
| S1 | `kelly_sizing` module | `compute_kelly_size(confidence_pct, balance, tier, settings) -> float`. Pure function. |
| S2 | Balance tier classification | `classify_balance_tier(balance_usdt) -> Literal["small", "medium", "large", "whale"]`. Buckets: <$1k / $1k-$10k / $10k-$100k / 100k+. |
| S3 | `users.balance_tier` cache column | Schema migration: add `balance_tier TEXT NOT NULL DEFAULT 'small'`. Backfilled lazily on next `build_user_context`. |
| S4 | Multi-entry split | `split_entries(margin_usdt, confidence_pct, settings) -> list[float]`. When confidence < threshold (default 0.75): split into 2-3 tranches. |
| S5 | Dispatcher integration | After all gates, replace fixed `compute_position_margin` call with `compute_dynamic_size(...)`. Default-OFF flag preserves pre-PR9 behavior. |
| S6 | Multi-entry order placement | Existing dispatcher places ONE order; PR9's multi-entry path places N orders sequentially. Failure of tranche N>1 logs + leaves tranche 1 in place (no rollback — exchange ate the fee). |
| S7 | Telegram alert routing | `app/ops/alerts.py` extended: stateful-worker watchdog alerts route to Telegram when configured (env `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`), falls back to SMTP, then to logs. Strict precedence. |
| S8 | Watchdog alert escalation | `worker_watchdog_task` calls `alert_admin(level=...)` with severity. PR9 adds `severity="critical"` for stateful workers — critical alerts go straight to Telegram. |
| S9 | API surface | `/bot-status/sizing` returns the dispatcher's expected size for the next signal on each universe symbol (read-only diagnostic). |
| BENCH | Dispatcher sizing latency | `bench_dispatcher_sizing.py` measures `compute_dynamic_size` Δp50/Δp99. Tight budget: Δp50 ≤ 2ms, Δp99 ≤ 10ms (pure function). |
| DEFERRED | True `p_win` integration | Forward-compat flag `SIZING_USE_P_WIN_WHEN_AVAILABLE=True`. Path is `sizing.py:_resolve_p_win()` which checks `predict_p_win(...)` first and falls back to `confidence_pct / 100.0`. When PR5 lands, no PR9 code changes. |
| DEFERRED | Stateful worker auto-restart | Requires in-memory state migration design (live_worker holds open positions; shadow_worker holds per-(sym,tf) bars; ws_keepalive holds socket state). Separate PR. |
| DEFERRED | FU-2 / FU-3 | Independent investigations. Stay in `KNOWN_ISSUES.md`. |

---

## 3. Architecture

### 3.1 Sizing decision flow

```
SignalProposal arrives → dispatcher.dispatch()
                              ↓
              ... pre-conditions (funding/cooldown/MTF/SHORT) ...
                              ↓
              ... position-sizing block (NEW PR9 path) ...
                              ↓
   ┌──────────────────────────────────────────────────────┐
   │  DYNAMIC_SIZING_ENABLED=False (default)              │
   │    → compute_position_margin(...) [existing]         │
   │  DYNAMIC_SIZING_ENABLED=True                         │
   │    → compute_dynamic_size(...) [new]                 │
   │      1. resolve p_win (confidence proxy OR PR5 real) │
   │      2. classify balance tier                         │
   │      3. kelly fraction × tier cap × hard cap          │
   │      4. split into N tranches if confidence < thresh  │
   │    → returns list[float] (1 elt if no split)         │
   └──────────────────────────────────────────────────────┘
                              ↓
              ... place order(s) — N>1 = sequential ...
```

### 3.2 Kelly-fractional formula

```
edge       = (2 * p_win - 1)
            // p_win=0.5 → edge=0 (no bet); p_win=1.0 → edge=1.0 (max bet)
kelly_pct  = edge / odds_ratio       // odds_ratio = 1.0 assumed (1:1 risk:reward)
            // simpler than full Kelly for 1:1 binary outcome
            // edge=0.5 → kelly=50% of bankroll — TOO AGGRESSIVE.
            // We multiply by fractional_kelly (defaults 0.25 = quarter-Kelly).
fraction   = kelly_pct * SIZING_FRACTIONAL_KELLY  // default 0.25
fraction   = min(fraction, TIER_MAX_FRACTION[tier])
            // small=0.01, medium=0.02, large=0.05, whale=0.1
fraction   = max(fraction, 0.0)  // never negative
margin_usdt = balance_usdt * fraction
            // clamped to [user.fixed_size_min_usdt, user.fixed_size_max_usdt]
```

This is **quarter-Kelly** by default. Even at 60% confidence (p_win=0.6, edge=0.2, kelly=20%, quarter=5%), a small-tier user gets capped to 1% of bankroll. Whale tier (≥$100k) can take up to 10% of bankroll. Defensive on purpose — operator can override `SIZING_FRACTIONAL_KELLY` per env, but the per-tier cap is a structural floor against fat-finger error.

### 3.3 Multi-entry split

When `confidence_pct < SIZING_MULTI_ENTRY_THRESHOLD` (default 0.75):
- Split total margin into N tranches (default 2 tranches at 60/40, configurable via `SIZING_MULTI_ENTRY_RATIOS=[0.6, 0.4]`).
- Tranche 1 placed immediately at the signal's `entry_price`.
- Tranche 2 placed only if price moves to `entry_price ± SIZING_MULTI_ENTRY_DCA_BAND_PCT` (default 0.5%) on the unfavorable side. If price runs away to TP first, tranche 2 never fires (operator captures less than full size but at the better entry).
- Tranche 2 uses the SAME stop-loss + take-profit as tranche 1 (same risk envelope).

**Failure mode**: tranche 2 placement fails (network blip, rate limit). Tranche 1 stays in place. The position is just smaller than designed — not a safety issue. Log warning + continue.

### 3.4 Telegram alert routing

```
alert_admin(level="critical", message="..."):
  1. If TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID set:
     → send Telegram message via existing telegram_send_message()
     → on success: return
  2. Else if SMTP configured:
     → send email
     → on success: return
  3. Else: log critical message
```

Stateful-worker watchdog alerts go through `level="critical"`. Stateless-worker watchdog alerts stay `level="warning"` and continue routing via SMTP-then-logs.

### 3.5 Fail-open contract (sizing)

If `compute_dynamic_size` raises:
- Log error with full traceback.
- **Fall back to `compute_position_margin`** (the pre-PR9 path). Trade still happens at fixed/percent size.
- Defends against malformed user data, unexpected confidence values, or a buggy Kelly compute.

This is **fail-open to the old behavior**, not fail-open to "no trade" — sizing failures should NOT silently DOS trading.

---

## 4. File structure

### 4.1 Created

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/2026_05_18_0023_pr9_users_balance_tier.py` | Add `users.balance_tier TEXT NOT NULL DEFAULT 'small'`. 4-step pattern. |
| `backend/app/trading/dynamic_sizing.py` | Pure functions: `classify_balance_tier`, `compute_kelly_fraction`, `compute_dynamic_size`, `split_entries`. |
| `backend/app/trading/multi_entry.py` | `place_multi_entry_orders(...)` — sequential tranche placement; logs + continues on tranche-N>1 failure. |
| `backend/app/ops/alert_routing.py` | New module: `alert_admin(level, message)` with Telegram→SMTP→logs precedence. Existing `alert_admin` call sites continue to work. |
| `backend/tests/db/test_pr9_migration.py` | Postgres introspection: column exists, default, NOT NULL, downgrade round-trip. |
| `backend/tests/db/test_pr9_migration_downgrade.py` | Round-trip. |
| `backend/tests/unit/test_pr9_settings_defaults.py` | All 8 new settings have correct defaults. |
| `backend/tests/unit/test_dynamic_sizing.py` | Kelly math, tier caps, p_win proxy resolution, fail-open on bad data. |
| `backend/tests/unit/test_multi_entry_split.py` | Split ratios, tranche count, no-split-above-threshold. |
| `backend/tests/trading/test_multi_entry_orders.py` | Tranche 2 placement happens on DCA band; failure of tranche 2 doesn't roll back tranche 1. |
| `backend/tests/trading/test_dispatcher_pr9_sizing_integration.py` | DYNAMIC_SIZING_ENABLED toggle path; fail-open to old sizing on compute error. |
| `backend/tests/ops/test_alert_routing.py` | Telegram first, SMTP fallback, log fallback, level routing. |
| `backend/tests/integration/test_pr9_e2e_kelly_size.py` | E2E: small-tier user with 0.7 confidence signal → quarter-Kelly × 0.01 cap × balance. |
| `backend/scripts/bench_dispatcher_sizing.py` | V-7 bench (Δp50 ≤ 2ms, Δp99 ≤ 10ms). |

### 4.2 Modified

| Path | Reason |
|---|---|
| `backend/app/config.py` | Add 8 PR9 settings (see §5). |
| `backend/app/trading/execution/dispatcher.py` | After all gates, route through `compute_dynamic_size` when `DYNAMIC_SIZING_ENABLED=True`. Fail-open to existing path. |
| `backend/app/trading/execution/glue.py` | `compute_position_margin` becomes the legacy/fallback path; new path via dynamic_sizing module. |
| `backend/app/auth/build_user_context.py` (or equivalent) | Backfill `users.balance_tier` lazily based on current portfolio_value_usdt. |
| `backend/app/ops/worker_supervisor.py` | Watchdog alerts route through new `alert_routing.alert_admin` with `level="critical"` for stateful workers. |
| `backend/app/api/routes/bot_status.py` | New `/sizing` endpoint. |
| `backend/app/api/schemas.py` | `SizingPreviewOut` schema. |
| `docs/ARCHITECTURE.md` | New §11d — Dynamic sizing + alert routing. |
| `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md` | Update PR9 section to reflect actual landed scope. |

---

## 5. Settings (new)

```python
# --- PR9 dynamic sizing -------------------------------------------------
# Default OFF for prod safety. Operator flips per env after 7-day soak.
DYNAMIC_SIZING_ENABLED: bool = False

# Use predict_p_win() if/when available; otherwise fall back to
# confidence_pct/100.0. Forward-compat for PR5 (p_win calibrator).
SIZING_USE_P_WIN_WHEN_AVAILABLE: bool = True

# Quarter-Kelly is the default. Operator can lower to eighth-Kelly per
# env for an even-more-defensive deployment.
SIZING_FRACTIONAL_KELLY: float = 0.25

# Per-tier hard caps as fraction of bankroll. Structural floor — Kelly
# CANNOT exceed these regardless of confidence.
SIZING_TIER_MAX_FRACTION: dict[str, float] = {
    "small": 0.01,   # <$1k — 1% max per trade
    "medium": 0.02,  # $1k-$10k — 2% max
    "large": 0.05,   # $10k-$100k — 5% max
    "whale": 0.10,   # 100k+ — 10% max
}

# Tier bucket boundaries (USDT).
SIZING_TIER_BOUNDARIES: dict[str, float] = {
    "small_max": 1_000.0,
    "medium_max": 10_000.0,
    "large_max": 100_000.0,
}

# Multi-entry split kicks in when confidence < threshold.
SIZING_MULTI_ENTRY_THRESHOLD: float = 0.75

# Tranche ratios — must sum to 1.0.
SIZING_MULTI_ENTRY_RATIOS: list[float] = [0.6, 0.4]

# DCA band — tranche 2 placed when price moves this pct against signal.
SIZING_MULTI_ENTRY_DCA_BAND_PCT: float = 0.5
```

---

## 6. Schema

```sql
-- 4-step pattern
ALTER TABLE users ADD COLUMN balance_tier TEXT NULL;
UPDATE users SET balance_tier = 'small' WHERE balance_tier IS NULL;
ALTER TABLE users ALTER COLUMN balance_tier SET NOT NULL;
ALTER TABLE users ALTER COLUMN balance_tier SET DEFAULT 'small';
```

Backfilled on next `build_user_context()` invocation based on current `portfolio_value_usdt`. Cached in the column so the dispatch hot path doesn't re-classify per signal.

---

## 7. Test surface

**~80 cases across 11 files:**

- Migration introspection + downgrade round-trip (6)
- Settings defaults (10)
- `classify_balance_tier` boundary tests (5)
- `compute_kelly_fraction` matrix (12) — confidence edge cases, tier caps, fractional-Kelly variations
- `compute_dynamic_size` E2E (8) — balance × confidence × tier integration
- `split_entries` (6) — no-split above threshold, 2-tranche, 3-tranche, sum-to-total
- `place_multi_entry_orders` (5) — tranche 2 fires on DCA band, tranche 2 failure doesn't roll back, tranche 1 SL+TP preserved
- Dispatcher integration (6) — DYNAMIC_SIZING_ENABLED toggle, fail-open to fixed-USDT on compute error
- Alert routing (8) — Telegram first, SMTP fallback, log fallback, severity routing, partial-config edge cases
- E2E Kelly (4) — small-tier $500 user with 0.7 confidence signal → 0.7→edge 0.4→kelly 0.4→quarter 0.1→tier-capped 0.01→$5
- Bench (1)
- Pre-existing test regression (verify dispatcher tests still pass with new path inserted)

---

## 8. Operator decision points

§8 captures choices I made unilaterally — operator can redirect each before plan-write.

1. **Tier boundaries**: $1k / $10k / $100k. Reasonable but arbitrary; could be operator-specific. Adjustable via `SIZING_TIER_BOUNDARIES`.
2. **Tier max fractions**: 1% / 2% / 5% / 10%. Conservative ladder. Whale tier (10%) is aggressive for a single trade.
3. **Default fractional-Kelly = 0.25** (quarter-Kelly). Industry-standard defensive setting. Half-Kelly would scale 2x larger.
4. **Multi-entry default 2 tranches at 60/40**. Could be 70/30 or 50/50.
5. **DCA band 0.5%**. Tight — many signals won't pull back this much.
6. **Multi-entry SAME SL+TP for all tranches**. Alternative: trail SL toward avg entry. PR9 keeps it simple.
7. **`SIZING_USE_P_WIN_WHEN_AVAILABLE=True` default**. When PR5 lands, sizing auto-switches. Operator could prefer manual toggle.
8. **Telegram alert routing precedence: Telegram > SMTP > logs**. If both configured, Telegram wins. Operator might prefer "always send to both."
9. **Fail-open contract: sizing errors fall back to pre-PR9 fixed/percent path**, not to "no trade." Trading continues at the safer-known size. Could be "no trade" instead.

---

## 9. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Kelly compute returns a size larger than user's actual Binance balance | HIGH | Tier cap + min/max user clamps + Binance order rejection (handled by existing dispatcher error path). |
| Multi-entry tranche 2 fires on cascade — operator gets DOUBLE the size at WORSE price | HIGH | DCA band default 0.5% is small enough that two tranches in a cascade are still "one trade size." |
| Telegram bot down — critical stateful-worker alert lost | MEDIUM | SMTP fallback chains automatically. Log line always emitted regardless. |
| Balance tier cache stale (user moved tiers since last `build_user_context`) | LOW | Backfilled lazily; PR9 also re-evaluates on the dispatch hot path (one column read). Worth the latency. |
| 7-day soak misses a corner case that only emerges with cold-start traffic | MEDIUM | Operator carve-out: dev→main requires explicit ship-it. Soak surfaces the corner cases; operator decides whether to ship. |

---

## 10. Out of scope (deferred)

- **True p_win integration** — PR5. Forward-compat hook lands in PR9.
- **Stateful worker auto-restart** — needs in-memory state migration design. Separate PR.
- **FU-2 (audit chain v2)** — JSONB canonical hashing + Telegram alert routing for verifier alerts. Independent.
- **FU-3 (verifier stability)** — 5-each-in-8-seconds investigation. Independent.
- **Per-direction sizing** — long vs short might warrant different Kelly fractions. Operator can revisit if soak shows asymmetry.
- **Cross-asset correlation cap** — if 5 BTC-correlated assets all fire simultaneously, Kelly per-trade × 5 could over-expose. PR9 leaves this to `max_concurrent_positions`. Per-correlation cap is a future PR.

---

## 11. Acceptance criteria

PR9 ships to **dev** when:
- [ ] All ~80 tests pass; lint + mypy clean.
- [ ] V-7 bench passes (Δp50 ≤ 2ms, Δp99 ≤ 10ms).
- [ ] Default-OFF in prod (DYNAMIC_SIZING_ENABLED=False) — bit-identical to pre-PR9 at deploy.
- [ ] Audit chain replay-identity verifies (no new hashed columns; balance_tier on users is read-mostly).
- [ ] ARCHITECTURE.md §11d published.

PR9 ships to **main** when (operator carve-out — explicit ship-it required):
- [ ] 7-day staging soak completes with flag flipped ON in staging.
- [ ] Operator reviews staging trade ledger: actual sizes match Kelly predictions within tolerance.
- [ ] Operator explicitly approves "merge to main." No auto-promotion.
- [ ] Cherry-pick prod-promotion (permanent auth for the pattern itself; the merge button is operator-only for PR9).
- [ ] After main merge: prod soak with flag OFF first; operator flips per their schedule.

---

**End of design draft.** Operator: review §8 (9 decision points) before plan-write. PR9 is the most-sensitive PR in the rollout — live-money exposure.
