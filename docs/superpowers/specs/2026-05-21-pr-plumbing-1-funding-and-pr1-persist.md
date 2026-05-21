# PR-PLUMBING-1 — funding_rate threading + PR1 fields on shadow_open_positions

**Status:** spec drafted 2026-05-21. Bundle of 2 dead-code/plumbing fixes from the post-PR-strategy-1 code-aware brainstorm.

**Branch:** `feat/pr-plumbing-1-funding-and-pr1-persist` (off `origin/dev` at `2587d12` = PR-strategy-1 squash).

**Class:** observability + recording-only. Default OFF flag states UNCHANGED. Dispatcher decision logic unchanged except that the existing funding-rate guard now sees real values instead of zero. Attribution-safe vs PR-strategy-1's 7-day validation window.

---

## Why ship now

PR-strategy-1 is currently soaking (`DISABLE_SHORT_SIGNALS=true` flipped 2026-05-21T19:42:22Z; 5h verify in progress). PR-PLUMBING-1 lands ALONGSIDE the soak because neither fix can mask PR-strategy-1's measurable effect:

- **Fix 1 (funding gate)** uses an existing kill-switch threshold (`KILL_DEFAULTS["funding_rate_guard"]=0.01`, 1%/day). Pre-PR-PLUMBING-1 the gate evaluated `daily=0.0 > 0.01` → never tripped. Post-fix it evaluates real rates. At current Binance funding levels (~0.01% per 8h = 0.03%/day on majors) the gate STILL never trips — threshold is conservatively high. Behavioral effect on entry-rate is ~zero unless operator deliberately tightens the threshold in a later PR.
- **Fix 3 (PR1 fields on `shadow_open_positions`)** affects only data quality for restart-survivor positions. No WR change, no entry-rate change.

---

## Fix 1: Wire `funding_rate_daily` through `proposal_from_prediction`

### Problem

`backend/app/trading/execution/dispatcher.py:373-388` defines `_check_funding_block` which calls `evaluate_funding_rate(daily_funding_rate=proposal.funding_rate_daily, ...)`. But `proposal_from_prediction` (`backend/app/trading/execution/glue.py:151-193`) never threads the rate — `SignalProposal.funding_rate_daily` defaults to 0.0 (`dispatcher.py:98`). The guard runs on every signal and ALWAYS evaluates `0.0 > 0.01 → False`. Dead since SP-8 Phase J.

### Verified facts (from recon at deployed `295aa70`):

- `LivePredictionOut` does **NOT** carry the raw funding rate — only the derived `funding_directional_adj: float | None` (the ±0.10 score boost). The rate is computed at `predictor.py:363-369` via `lookup_latest_funding_rate(...)`, consumed for the boost, then discarded.
- `evaluate_funding_rate` semantics: expects rate in **per-day fraction** (0.01 = 1%/day). Threshold `KILL_DEFAULTS["funding_rate_guard"] = 0.01`.
- `lookup_latest_funding_rate` returns the raw **per-8h Binance funding rate** (fapi `premiumIndex` convention).
- Conversion: `daily = rate_8h * 3` (3 funding events per day).

### Fix

1. **Add `funding_rate_daily: float | None = None` to `LivePredictionOut`** (`app/api/schemas.py:159` block).
2. **Populate in `build_prediction`** — capture the `funding_rate` local already computed at `predictor.py:363`, multiply by 3 for daily semantics, set on `LivePredictionOut(..., funding_rate_daily=funding_rate * 3 if funding_rate is not None else None)`. None when intermarket lookup failed.
3. **Add `pred_funding_rate_daily: float | None = None` kwarg to `proposal_from_prediction`** (`app/trading/execution/glue.py`). When provided, pass into `SignalProposal(..., funding_rate_daily=pred_funding_rate_daily or 0.0)`. The `or 0.0` preserves the existing default for callers that don't supply (admin_test_trade, telegram callback).
4. **Update caller in `app/ws/live_prediction.py`** (around line 332): add `"pred_funding_rate_daily": pred.funding_rate_daily,` to `proposal_kwargs`.

### Tests (Fix 1)

- `test_proposal_from_prediction_threads_funding_rate` — `pred_funding_rate_daily=0.03` → `SignalProposal.funding_rate_daily == 0.03`
- `test_proposal_from_prediction_funding_rate_none_defaults_zero` — `pred_funding_rate_daily=None` → `SignalProposal.funding_rate_daily == 0.0` (backwards compat)
- `test_funding_block_evaluates_with_real_rate` — patch `evaluate_funding_rate`; assert `daily_funding_rate` kwarg matches `proposal.funding_rate_daily` not 0.0
- `test_existing_zero_funding_signal_passes_gate` — regression: a legitimately-zero rate still passes (no false trip)
- `test_build_prediction_sets_funding_rate_daily_when_lookup_returns_rate` — patch `lookup_latest_funding_rate` to return 0.01 (per-8h); assert `LivePredictionOut.funding_rate_daily == 0.03` (3× conversion)
- `test_build_prediction_funding_rate_daily_none_when_lookup_fails` — patch lookup to raise/return None; assert `funding_rate_daily=None`

---

## Fix 3: Persist PR1 fields on `shadow_open_positions`

### Problem

PR-strategy-1 plumbed 7 PR1 fields (`mtf_agreement`, `mtf_dominant_tf`, `mtf_directions_json`, `p_win`, `effective_score`, `realized_vol_20d`, `funding_directional_adj`) through `ShadowPosition` → `persist_closed_trade` → `build_shadow_trade_payload`. But `shadow_open_positions` table has none of these columns. Restart cycle = `list_open_positions` reconstructs `ShadowPosition` with PR1 fields = None (their dataclass defaults). When that position later closes, the `shadow_trades` row has NULL PR1 columns.

### Fix

1. **New alembic migration** `2026_05_21_NNNN_pr_plumbing_1_pr1_on_shadow_open_positions.py`. Add 7 nullable columns matching `shadow_trades` types:
   - `mtf_agreement` smallint
   - `mtf_dominant_tf` varchar
   - `mtf_directions_json` jsonb
   - `p_win` real
   - `effective_score` real
   - `realized_vol_20d` real
   - `funding_directional_adj` real

   Revision id must fit VARCHAR(32) (`alembic_version` limit). Use `0025_pr_plumbing_1_op_pr1_cols` (29 chars). Down_revision = current head.

2. **Update `persist_open_position`** (`app/shadow/persistence.py:38-68`) — extend INSERT column list + values, pulling from `pos.mtf_agreement` etc. with `getattr(pos, ..., None)` defensive reads.

3. **Update `list_open_positions`** (`app/shadow/persistence.py:71-100`) — read 7 columns from the row via `getattr(r, ..., None)` and populate the reconstructed `ShadowPosition`. The PR3 pattern (`tf = getattr(r, "timeframe", None) or "1h"`) is the template.

4. **No code change needed in worker open path** — `shadow/worker.py::_maybe_open_position` already assigns the 7 fields onto `position` (PR-strategy-1 wiring) before `persist_open_position(session, position, ...)` is called. The persistence layer just needs to write them.

5. **Audit chain:** `shadow_open_positions` is NOT a hash-chained table (see `app/db/audit.py::HASH_PAYLOAD_COLUMNS` — only 8 tables listed; `shadow_open_positions` isn't one). No `NON_HASHED_ALLOW_LIST` change needed. The new columns are recording-only state.

### Tests (Fix 3)

- `test_alembic_migration_adds_7_columns_to_shadow_open_positions` — Postgres-only introspection (SQLite would also work via PRAGMA). Skip on SQLite if needed.
- `test_alembic_migration_downgrade_drops_columns` — round-trip
- `test_persist_open_position_writes_pr1_fields` — set 7 fields on `ShadowPosition`, persist, SELECT raw row, assert all 7 values match
- `test_persist_open_position_writes_nulls_when_pr1_fields_unset` — defaults-None case
- `test_load_open_positions_reads_pr1_fields_back` — persist with PR1 values, list_open_positions, assert reconstructed `ShadowPosition` carries the values
- `test_full_round_trip_persists_pr1_fields_through_restart` — integration: persist_open → list_open (simulate restart) → close trade → SELECT shadow_trades row → assert 7 columns match
- `test_legacy_open_positions_load_with_nulls` — INSERT a row with NULL PR1 columns, list_open_positions, assert `ShadowPosition` fields are None (no AttributeError, backward compat)

---

## Combined audit-chain impact

**None.** `shadow_open_positions` is not chained. Existing chained tables (`shadow_trades` etc.) are touched only via `persist_closed_trade` which already wrote these 7 fields per PR-strategy-1 — no schema change there.

## V-7 latency budget

- Fix 1: one extra dict-key thread + one multiply-by-3 per prediction. Sub-microsecond.
- Fix 3: 7 extra columns in the INSERT and SELECT for `shadow_open_positions`. Tiny.

Dispatcher hot path unchanged.

## Rollback

`git revert <PR-PLUMBING-1-squash>`. Migration downgrade is reversible (drops the 7 columns; data loss for any row written between deploy and revert). Funding gate reverts to dead-code state.

## Out of scope (deferred)

- Backfill PR1 fields on existing-open `shadow_open_positions` rows (operator directive: "no backfill — those stay NULL until they close").
- Tightening `KILL_DEFAULTS["funding_rate_guard"]` to use real-world thresholds (separate PR after observing real-rate distribution).
- Switching `entry_quality` gate to read `effective_score` instead of `signal.score` (Phase 4 work per brainstorm J).
- Trailing stops / structure SL (later phases).

## Auto-merge authorization

Per operator's PR-PLUMBING-1 directive: class = "bug fix + recording-only schema add, NOT behavior-changing — flag default state unchanged, dispatcher behavior unchanged except funding gate now uses real values." 12h observability soak required (one nightly cron cycle).

## Commit message

`feat(pr-plumbing-1): wire funding_rate_daily + persist PR1 fields on shadow_open_positions`
