# PR9 — Dynamic sizing + Telegram alert routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Kelly-fractional sizing scaled by confidence × balance tier × hard caps. Multi-entry split for sub-threshold confidence. Telegram alert routing for stateful-worker critical alerts. All default-OFF in prod (`DYNAMIC_SIZING_ENABLED=False`).

**Architecture:** Pure-function `compute_dynamic_size(...)` in new `dynamic_sizing.py` module. Dispatcher routes through it after all pre-condition gates, with fail-open to pre-PR9 `compute_position_margin` on any exception. Multi-entry split places N sequential orders; tranche-N>1 failure leaves earlier tranches in place. Telegram routing wraps existing alert paths via new `alert_routing.alert_admin` (Telegram > SMTP > logs precedence). Forward-compat hook for PR5's real `p_win` integration via `SIZING_USE_P_WIN_WHEN_AVAILABLE`.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Pydantic v2 / Alembic / pytest + pytest-asyncio.

**Source spec:** [`docs/superpowers/specs/2026-05-18-pr9-dynamic-sizing-self-healing-design.md`](../specs/2026-05-18-pr9-dynamic-sizing-self-healing-design.md)

**Branch:** `feat/pr9-impl-dynamic-sizing-self-healing` off `dev` (which after PR8 lands will contain PR3+PR8). NEVER push to `main`.

**Behavior change classification:** YES + LIVE-MONEY. Per operator carve-out: 7-day staging soak required; dev→main requires explicit operator "ship it" — the ONLY exception in the rollout. Default-OFF at deploy preserves pre-PR9 behavior bit-for-bit.

---

## File Structure (locked in via design)

### NEW files

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/2026_05_18_0023_pr9_users_balance_tier.py` | Add `users.balance_tier TEXT NOT NULL DEFAULT 'small'` — 4-step pattern. |
| `backend/app/trading/dynamic_sizing.py` | Pure: classify_balance_tier, _resolve_p_win, compute_kelly_fraction, compute_dynamic_size, split_entries. |
| `backend/app/trading/multi_entry.py` | `place_multi_entry_orders` — sequential tranche placement. |
| `backend/app/ops/alert_routing.py` | `alert_admin(level, message)` with Telegram → SMTP → logs precedence. |
| `backend/tests/db/test_pr9_migration.py` | Postgres introspection: column, default, NOT NULL. |
| `backend/tests/db/test_pr9_migration_downgrade.py` | Round-trip upgrade → downgrade → upgrade. |
| `backend/tests/unit/test_pr9_settings_defaults.py` | All 8 settings + LiveExitReason-style sanity (every tier in TIER_MAX_FRACTION). |
| `backend/tests/unit/test_dynamic_sizing.py` | Kelly math + tier caps + p_win proxy resolution + fail-open. |
| `backend/tests/unit/test_multi_entry_split.py` | Split ratios, tranche count, no-split-above-threshold, ratios-sum-to-1.0 invariant. |
| `backend/tests/trading/test_multi_entry_orders.py` | DCA band trigger; tranche 2 failure isolation. |
| `backend/tests/trading/test_dispatcher_pr9_sizing_integration.py` | DYNAMIC_SIZING_ENABLED toggle; fail-open to old path. |
| `backend/tests/ops/test_alert_routing.py` | Telegram → SMTP → log precedence; level routing. |
| `backend/tests/integration/test_pr9_e2e_kelly_size.py` | Small-tier $500 user × 0.7 confidence → Kelly + cap. |
| `backend/scripts/bench_dispatcher_sizing.py` | V-7 bench (Δp50 ≤ 2ms, Δp99 ≤ 10ms). |

### MODIFIED files

| Path | Reason |
|---|---|
| `backend/app/config.py` | Add 8 PR9 settings. |
| `backend/app/trading/execution/dispatcher.py` | After gates: route through `compute_dynamic_size` when enabled; fail-open to existing path. |
| `backend/app/trading/execution/glue.py` | Existing `compute_position_margin` becomes legacy/fallback path. |
| `backend/app/trading/execution/build_user_context.py` (path TBD) | Lazy backfill `users.balance_tier`. |
| `backend/app/ops/worker_supervisor.py` | Watchdog alerts use new `alert_routing.alert_admin(level="critical")` for stateful workers. |
| `backend/app/api/routes/bot_status.py` | New `/sizing` endpoint. |
| `backend/app/api/schemas.py` | `SizingPreviewOut`. |
| `docs/ARCHITECTURE.md` | New §11d. |
| `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md` | Reflect actual landed scope. |

---

## Phase 1: Alembic migration

**Files:** Create `2026_05_18_0023_pr9_users_balance_tier.py` + 2 test files.

- [ ] **1.1** Write failing migration introspection test (column exists, NOT NULL, DEFAULT 'small').
- [ ] **1.2** Run test — FAIL.
- [ ] **1.3** Write migration — 4-step pattern (ADD NULLABLE → backfill 'small' → SET NOT NULL → SET DEFAULT). Reversible downgrade.
- [ ] **1.4** Apply migration + re-run tests — PASS.
- [ ] **1.5** Write downgrade round-trip test; PASS.
- [ ] **1.6** Commit: `feat(pr9): alembic — users.balance_tier (Phase 1)`

---

## Phase 2: 8 settings + tier classifier

**Files:** Modify `app/config.py`; create `app/trading/dynamic_sizing.py` (skeleton with `classify_balance_tier` only); create test file.

- [ ] **2.1** Write failing test for `classify_balance_tier`:
  - `< $1k` → "small"
  - `$1k - $10k` → "medium"
  - `$10k - $100k` → "large"
  - `≥ $100k` → "whale"
  - boundary cases at $999, $1000, $9999, $10000, $99999, $100000.
- [ ] **2.2** Run — FAIL (module doesn't exist).
- [ ] **2.3** Add 8 settings to `app/config.py`:
  - `DYNAMIC_SIZING_ENABLED: bool = False`
  - `SIZING_USE_P_WIN_WHEN_AVAILABLE: bool = True`
  - `SIZING_FRACTIONAL_KELLY: float = 0.25`
  - `SIZING_TIER_MAX_FRACTION: dict[str, float] = {...}`
  - `SIZING_TIER_BOUNDARIES: dict[str, float] = {...}`
  - `SIZING_MULTI_ENTRY_THRESHOLD: float = 0.75`
  - `SIZING_MULTI_ENTRY_RATIOS: list[float] = [0.6, 0.4]`
  - `SIZING_MULTI_ENTRY_DCA_BAND_PCT: float = 0.5`
- [ ] **2.4** Create `app/trading/dynamic_sizing.py` skeleton with `classify_balance_tier` only.
- [ ] **2.5** Settings defaults test (verifies all 8 land with spec values + every tier in TIER_MAX_FRACTION).
- [ ] **2.6** Run — PASS.
- [ ] **2.7** Commit: `feat(pr9): 8 settings + classify_balance_tier (Phase 2)`

---

## Phase 3: Pure-function Kelly compute

**Files:** Extend `app/trading/dynamic_sizing.py` with `_resolve_p_win`, `compute_kelly_fraction`, `compute_dynamic_size`; extend test file.

- [ ] **3.1** Write failing tests for `_resolve_p_win`:
  - PR5 `predict_p_win` returns None → fall back to `confidence_pct/100`.
  - PR5 `predict_p_win` returns 0.7 → use 0.7 directly (forward-compat).
  - Flag `SIZING_USE_P_WIN_WHEN_AVAILABLE=False` → always use confidence proxy.
- [ ] **3.2** Write failing tests for `compute_kelly_fraction(p_win, tier, settings)`:
  - p_win=0.5 → fraction=0 (no edge).
  - p_win=1.0 → fraction = quarter-Kelly × full-edge = 0.25, then tier-capped.
  - p_win=0.7, tier="small" → kelly_pct=0.4, fractional=0.1, tier-cap=0.01 → 0.01.
  - p_win=0.7, tier="whale" → kelly_pct=0.4, fractional=0.1, tier-cap=0.10 → 0.10.
  - p_win < 0.5 (edge negative) → fraction=0.
  - Eighth-Kelly via `SIZING_FRACTIONAL_KELLY=0.125`.
- [ ] **3.3** Write failing tests for `compute_dynamic_size(balance, p_win, confidence_pct, settings)`:
  - balance=$500, confidence=70 → tier="small", quarter-Kelly capped to 1% = $5.
  - balance=$200k, confidence=70 → tier="whale", quarter-Kelly capped to 10% = $20k.
  - Disabled flag → returns None (caller falls back to existing path).
  - Compute error → returns None (fail-open).
- [ ] **3.4** Implement all three functions.
- [ ] **3.5** Run — PASS.
- [ ] **3.6** Commit: `feat(pr9): _resolve_p_win + compute_kelly_fraction + compute_dynamic_size (Phase 3)`

---

## Phase 4: Multi-entry split + ratios invariants

**Files:** Add `split_entries` to `app/trading/dynamic_sizing.py`; new test file.

- [ ] **4.1** Failing tests:
  - confidence ≥ 0.75 → returns `[total]` (no split).
  - confidence < 0.75 → returns N tranches per `SIZING_MULTI_ENTRY_RATIOS`.
  - Ratios sum != 1.0 → raises (configuration error).
  - Sum of tranches == total (rounding loss accumulates in last tranche).
- [ ] **4.2** Run — FAIL.
- [ ] **4.3** Implement.
- [ ] **4.4** Run — PASS.
- [ ] **4.5** Commit: `feat(pr9): split_entries (Phase 4)`

---

## Phase 5: Multi-entry order placement

**Files:** Create `app/trading/multi_entry.py`; create test file.

- [ ] **5.1** Failing tests for `place_multi_entry_orders`:
  - 1-tranche path: single order placed; returns single order_id.
  - 2-tranche path: tranche 1 placed at entry; tranche 2 placed when price moves ±DCA band against signal; SL+TP preserved.
  - Tranche 2 failure (binance rate limit) logs + returns tranche 1 only; tranche 1 not rolled back.
  - Price runs to TP before tranche 2 fires → tranche 2 never placed (operator captured less size but at the better entry).
- [ ] **5.2** Implement with mocked Binance client.
- [ ] **5.3** Run — PASS.
- [ ] **5.4** Commit: `feat(pr9): place_multi_entry_orders (Phase 5)`

---

## Phase 6: Dispatcher integration

**Files:** Modify `app/trading/execution/dispatcher.py` + `glue.py`; create dispatcher integration test.

- [ ] **6.1** Failing test: with `DYNAMIC_SIZING_ENABLED=True`, dispatcher returns size computed by `compute_dynamic_size`. With False, dispatcher returns size from existing `compute_position_margin`. With `compute_dynamic_size` raising, dispatcher falls back to `compute_position_margin` (fail-open).
- [ ] **6.2** Implement: after all pre-condition gates, branch on flag; call dynamic_sizing; on exception, log + fall through to legacy.
- [ ] **6.3** Run — PASS.
- [ ] **6.4** Regression: all existing dispatcher tests must still pass.
- [ ] **6.5** Commit: `feat(pr9): dispatcher routes through compute_dynamic_size when enabled (Phase 6)`

---

## Phase 7: Telegram alert routing

**Files:** Create `app/ops/alert_routing.py`; create test file; modify `worker_supervisor.py` and watchdog code path.

- [ ] **7.1** Failing tests:
  - Telegram configured → message sent via `telegram_send_message`; SMTP NOT called.
  - Telegram not configured, SMTP configured → SMTP called; log NOT called as primary.
  - Neither configured → log emitted at level=critical.
  - Telegram call raises → falls through to SMTP.
  - SMTP call raises → falls through to logs.
  - `level="warning"` → routes the SAME way (Telegram > SMTP > logs); level just controls the prefix.
- [ ] **7.2** Implement.
- [ ] **7.3** Modify `worker_supervisor.py` watchdog to call `alert_routing.alert_admin(level="critical", ...)` for stateful workers; `level="warning"` for stateless.
- [ ] **7.4** Run — PASS.
- [ ] **7.5** Regression: all watchdog tests must still pass.
- [ ] **7.6** Commit: `feat(pr9): Telegram alert routing for stateful-worker critical alerts (Phase 7)`

---

## Phase 8: /sizing API endpoint

**Files:** Modify `app/api/routes/bot_status.py` + `app/api/schemas.py`; create endpoint test.

- [ ] **8.1** Failing test: `/sizing` returns per-symbol expected size for next signal (read-only diagnostic) using the user's current balance + tier.
- [ ] **8.2** Add `SizingPreviewOut` schema.
- [ ] **8.3** Add route handler.
- [ ] **8.4** Run — PASS.
- [ ] **8.5** Commit: `feat(pr9): /bot-status/sizing endpoint + SizingPreviewOut (Phase 8)`

---

## Phase 9: V-7 latency bench

**Files:** Create `backend/scripts/bench_dispatcher_sizing.py`.

- [ ] **9.1** Mirror PR8's bench script structure. Modes: `--mode=baseline` (DYNAMIC_SIZING_ENABLED=False) vs `--mode=dynamic-on` (=True). Pre-seed users table with one row.
- [ ] **9.2** Measure Δp50 ≤ 2ms, Δp99 ≤ 10ms.
- [ ] **9.3** Run locally; confirm pass.
- [ ] **9.4** Commit: `bench(pr9): bench_dispatcher_sizing — V-7 PASS (Phase 9)`

---

## Phase 10: docs/ARCHITECTURE.md §11d + master rollout doc

**Files:** Modify `docs/ARCHITECTURE.md` + `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md`.

- [ ] **10.1** New §11d covering Kelly formula, tier ladder, multi-entry, fail-open, Telegram routing, soak requirement.
- [ ] **10.2** Update master rollout PR9 section to reflect actual landed scope (deferrals called out explicitly).
- [ ] **10.3** Commit: `docs(arch): section 11d — PR9 dynamic sizing + alert routing (Phase 10)`

---

## Self-review checklist (before opening PR)

- [ ] ~80 tests pass; lint + mypy clean.
- [ ] V-7 bench passes (Δp50 ≤ 2ms, Δp99 ≤ 10ms).
- [ ] Default-OFF in prod confirmed: with `DYNAMIC_SIZING_ENABLED=False`, dispatcher hot path identical to pre-PR9.
- [ ] Migration applies cleanly + downgrade round-trips.
- [ ] No regression in dispatcher / sizing / watchdog test suites.
- [ ] ARCHITECTURE.md §11d published.

---

## Execution handoff

Plan complete. After dev merge:
- **7-day staging soak required** (operator policy for live-money PRs).
- **dev→main requires explicit operator "ship it"** (carve-out — only PR in the rollout that demands this).
- Cherry-pick prod-promotion pattern still applies for the mechanics — but the merge BUTTON is operator-only for PR9.
