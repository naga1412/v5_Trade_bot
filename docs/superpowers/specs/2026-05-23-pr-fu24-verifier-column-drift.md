# PR-FU24-VERIFIER-COLUMN-DRIFT — Verifier whitelist-native + 8-table coverage

**Date:** 2026-05-23
**Branch:** `feat/pr-fu24-verifier-column-drift`
**Base:** `dev`
**Class:** infrastructure bug fix (verifier↔writer config alignment). NO trading-logic change. NO live behavior impact.

## Background

[PR-FU24-RACE-DEEP-INVESTIGATION V2](../../../tmp/pr-fu24-race-deep-investigation-20260523T093648Z.md) established that the daily 03:00 UTC `audit_chain_broken:predictions:1` and `audit_chain_broken:shadow_trades:1` alerts are NOT races. PR-SAFETY-BATCH-1's `pg_advisory_xact_lock` is correctly implemented and serializes concurrent writers as designed. The alerts come from a different cause: **the nightly verifier hashes a different column set than the writer did, so it can't reproduce the stored `row_hash`** and falsely reports a chain break on the first row of each verified table.

Two distinct sub-bugs:

1. **Column drift (predictions):** `audit.py` `HASH_PAYLOAD_COLUMNS["predictions"]` has 19 columns (11 base + 8 ghost_* + `model_checkpoint_id`). The verifier's local `CHAINED_TABLES["predictions"]` has only 11 — the original base set. Every row written after the ghost-candle feature shipped has stored `row_hash` reflecting 19 columns; the verifier recomputes from 11; mismatch → alert.
2. **Coverage gap:** `HASH_PAYLOAD_COLUMNS` registers 8 chained tables; `CHAINED_TABLES` covered only 3. Five tables (`live_trades`, `brain_decisions`, `tax_events`, `mode_change_log`, `symbol_performance_snapshots`) were silently unverified.

## Component A — Fix (this PR)

**File:** `backend/app/ops/verifier_scheduler.py`

- Delete the legacy `CHAINED_TABLES: dict[str, list[str]]` (was lines 56-111).
- Introduce `_tables_to_verify() -> Iterable[str]` returning `tuple(HASH_PAYLOAD_COLUMNS.keys())`. Single source of truth.
- Change `_check_all_chains` loop from `for table, columns in CHAINED_TABLES.items()` to `for table in _tables_to_verify()`.
- Change verify call from `verify_chain(session, table, columns=columns)` (LEGACY path: select listed columns only) to `verify_chain(session, table)` (WHITELIST-NATIVE path: `SELECT *` then filter via `HASH_PAYLOAD_COLUMNS`). The whitelist-native path is already implemented in `audit_verify.py:72-94`; no audit_verify changes.
- Update `__all__` (remove `CHAINED_TABLES`, add `_tables_to_verify`).
- Update module docstring to explain the change and the bonus 5-table coverage.

**Effect:**
- Writer and verifier share a single column list (`HASH_PAYLOAD_COLUMNS`). No more hand-sync, no more drift.
- All 8 chained tables verified, not 3.
- `predictions` and `shadow_trades` alarms at 03:00 UTC should stop for `predictions` immediately (column drift was the cause). For `shadow_trades` the column lists already matched, so this fix may or may not stop those alarms — if they persist, the residual cause is JSONB serialization drift (covered by Component B as a separate diagnostic — see "Out of scope" below).

## Test surface

**Updated existing tests:**
- `backend/tests/unit/test_ops_verifier_scheduler.py` — replace `CHAINED_TABLES` references with `HASH_PAYLOAD_COLUMNS`. Update fake `verify_chain` signatures to drop the `columns` kwarg.
- `backend/tests/integration/test_verifier_scheduler_detects_break.py` — replace `monkeypatch.setattr(vs, "CHAINED_TABLES", {...})` with `monkeypatch.setattr(vs, "_tables_to_verify", lambda: ("predictions",))`.

**New TDD tests:**
- `test_chained_tables_dict_removed` — regression: the legacy dict must not come back.
- `test_verifier_covers_all_chained_tables` — `_tables_to_verify()` returns every key in `HASH_PAYLOAD_COLUMNS`; count ≥ 8.
- `test_verifier_calls_verify_chain_without_columns_kwarg` — captures kwargs on each verify_chain invocation; asserts `"columns" not in kwargs`. Prevents accidental reintroduction of the legacy path.
- `test_verifier_reports_no_break_when_chain_actually_intact` — happy path: ok=True for every table → no alert, no auth_violations row.

**Local test results:**
- `tests/unit/test_ops_verifier_scheduler.py` (8 tests including the 4 new) + `tests/integration/test_verifier_scheduler_detects_break.py` (2 tests) → 10/10 pass.
- Broader audit test surface (`test_audit_hashchain`, `test_audit_race_fix`, `test_audit_replay_identity`, `test_audit_verifier_uses_whitelist`, `test_audit_whitelist`, `test_audit_whitelist_consistency`, `test_audit_verify` integration) → all green or appropriately skipped (Postgres-only / migrated-schema-only).

## Component B — DEFERRED (use existing `audit-replay-verify` probe instead)

The directive proposed a new `fu24-jsonb-drift-check` ops-debug probe (~30-50 LoC). On critique, this duplicates work already covered by the existing `audit-replay-verify` probe (workflow `ops-debug.yml` line 33), which re-hashes the last 100 rows of each chained table under 6 canonicalization strategies and reports match counts per strategy. That probe is what the prior investigation used to surface the "57/100 shadow_trades still fail" finding. Building a new probe is not worth the review surface when the existing one already answers the question.

**Post-deploy use:**
1. After this PR deploys, wait for the next 03:00 UTC verifier run.
2. Trigger `audit-replay-verify` via `ops-debug.yml`.
3. Read the report:
   - `predictions` matches at 100/100 under any strategy → Component A sufficient for predictions. ✅
   - `shadow_trades` matches at 100/100 → no JSONB drift; full fix achieved. ✅
   - `shadow_trades` still partial → JSONB drift is real; Component C (canonical JSON in the write path) becomes a follow-up PR.
4. Confirm 0 new `audit_chain_broken:predictions:*` rows in `auth_violations` over the next 24h via the `audit-history` probe.

## Risk surface

- **5 newly-verified tables.** First 03:00 UTC run after deploy may surface previously-hidden break alerts on `live_trades`, `brain_decisions`, `tax_events`, `mode_change_log`, or `symbol_performance_snapshots`. This is **expected and informative** — not a regression. Pre-flight via `audit-replay-verify` probe BEFORE this PR ships gives an early read on which tables (if any) will alarm.
- **No production write-path change.** `insert_with_chain` is untouched. Lock semantics unchanged. The change is read-side only.
- **Tests narrow the verifier via `_tables_to_verify`.** Tests that need to restrict the table set monkeypatch this function rather than the dict.

## Out of scope

- **Component B (new probe):** redundant with `audit-replay-verify`. Skipped.
- **Component C (canonical JSON in write path):** only needed if Component A leaves `shadow_trades` alarms in place. Deferred to a follow-up PR with prod data to inform the fix.
- **Healing historical rows:** stored `row_hash` values are correct at write time; this PR makes the verifier able to reproduce them. No backfill needed.

## Acceptance — post-deploy

- [ ] Next 03:00 UTC verifier run produces no new `audit_chain_broken:predictions:*` row in `auth_violations` (visible via `audit-history` probe).
- [ ] `audit-replay-verify` probe reports `predictions` matches at 100/100 under one of the 6 canonicalization strategies (any match counts; whitelist-native uses the same one the writer used).
- [ ] `shadow_trades` status documented in the post-deploy report (either fixed or Component C needed).
- [ ] No regression: no NEW spurious alarms on the previously-covered 3 tables.

## Files Changed

- `backend/app/ops/verifier_scheduler.py` — refactor (delete `CHAINED_TABLES`, add `_tables_to_verify`, switch to whitelist-native verify_chain call, update docstring + `__all__`).
- `backend/tests/unit/test_ops_verifier_scheduler.py` — update existing assertions; add 4 new tests.
- `backend/tests/integration/test_verifier_scheduler_detects_break.py` — update 2 monkeypatch sites.
- `docs/superpowers/specs/2026-05-23-pr-fu24-verifier-column-drift.md` — this document.
