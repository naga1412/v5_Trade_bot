# PR-MTF-DIRECTIONS-JSON-SERIALIZATION-FIX — Stop silent close-persistence failures

**Date:** 2026-05-23
**Branch:** `feat/pr-mtf-directions-json-serialization-fix`
**Base:** `dev`
**Class:** critical bug fix to persistence path. NO trading-logic change. NO live-trades impact.

## Symptom (prod, 2026-05-23)

After the PR-FU24 deploy / container restart at 10:21 UTC, the shadow worker began emitting:

```
ERROR app.shadow.worker: persist close failed for LTCUSDT/15m; suppressing publish:
(sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.DataError'>:
invalid input for query argument $27: {'1d': -1, '1h': 0, '1w': -1, '4h': -1, ...
('dict' object has no attribute 'encode')
```

Across LTCUSDT/15m, BNBUSDT/15m, NEARUSDT/15m, TAOUSDT/15m, INJUSDT/15m, LINKUSDT/15m, UNIUSDT/15m, ASTERUSDT/15m, DOGEUSDT/15m, ONDOUSDT/15m, ALTUSDT/15m, FIDAUSDT/15m, WLDUSDT/15m. `shadow_trades.id` counter stuck at 293 — every close attempt that has populated MTF directions data fails to persist; the position stays open; `shadow_trades` doesn't increment.

## Root cause

[`backend/alembic/versions/2026_05_21_0025_pr_plumbing_1_pr1_on_shadow_open_positions.py`](backend/alembic/versions/2026_05_21_0025_pr_plumbing_1_pr1_on_shadow_open_positions.py) added `mtf_directions_json` to `shadow_open_positions` as **JSONB** on Postgres / TEXT on SQLite.

The write path is fine:

1. Predictor at [`backend/app/core/predictor.py:403`](backend/app/core/predictor.py#L403) does `json.dumps(mtf.directions)` → string.
2. `LivePredictionOut.mtf_directions_json: str | None` (Pydantic enforces the type).
3. Shadow worker at [`backend/app/shadow/worker.py:664`](backend/app/shadow/worker.py#L664) copies the string onto `ShadowPosition.mtf_directions_json` (also `str | None`).
4. `persist_open_position` writes the string into JSONB; Postgres stores it as JSONB.

The bug is on the **read-back path**:

5. After a container restart (the PR-FU24 deploy was one), the in-memory open-positions cache is cold. The worker calls [`list_open_positions`](backend/app/shadow/persistence.py) to repopulate it.
6. asyncpg decodes the JSONB column into a Python **dict** (its default JSONB → Python codec).
7. [`backend/app/shadow/persistence.py:126`](backend/app/shadow/persistence.py#L126) assigns the dict directly back onto `ShadowPosition.mtf_directions_json`, violating the `str | None` type contract silently.
8. Next candle eval triggers a close → [`persist_closed_trade`](backend/app/shadow/persistence.py) → [`build_shadow_trade_payload`](backend/app/db/payload_builders.py) → `insert_with_chain` → asyncpg parameter-bind tries `value.encode(...)` on the dict → `DataError` → trade close suppressed → counter frozen.

## Live trades are NOT affected

[`backend/app/db/payload_builders.py:167-239`](backend/app/db/payload_builders.py#L167-L239) `build_live_trade_payload` takes `mtf_directions: dict[str, int] | None` (an explicit dict shape) and serialises in-function via `json.dumps(mtf_directions, sort_keys=True, separators=(",", ":"))` at line 235-238. The live path stores the canonical JSON string from the start and never reads back-and-forth through the bug-prone JSONB-decode path. **Live-trades close path is safe.**

This matters because PR-MTF-DIRECTIONS-JSON would otherwise be a 5/30 flip blocker. Verified clean.

## Fix

Two changes, both in this PR:

**A. Source fix — [`backend/app/shadow/persistence.py:list_open_positions`](backend/app/shadow/persistence.py)**: normalize the JSONB-decoded value back to the canonical JSON string at the read boundary so the `ShadowPosition.mtf_directions_json: str | None` contract holds. All downstream code can keep treating the field as a string.

**B. Defense-in-depth at the payload boundary — [`backend/app/db/payload_builders.py:build_shadow_trade_payload`](backend/app/db/payload_builders.py)**: also normalize at INSERT time so if any future code path bypasses (A), the bug can't reach asyncpg.

Both sites call the same helper:

```python
def _normalize_mtf_directions_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
```

Same canonical form the writer uses (`sort_keys=True`, compact separators). Stable across read/write round-trips. Recompute-friendly for the audit chain (relevant to the still-open Component B JSONB-drift investigation from PR-FU24).

## Other JSON columns audited

- **`shadow_trades.layer_scores`**: safe. [`build_shadow_trade_payload:137`](backend/app/db/payload_builders.py#L137) always does `json.dumps(pos.layer_scores)`. `list_open_positions` hardcodes `layer_scores={}` on the loaded position (it's not stored on `shadow_open_positions`) — no JSONB round-trip possible.
- **`shadow_open_positions.mtf_directions_json`**: the bug, fixed.
- **`live_trades.mtf_directions_json`**: safe (see "Live trades NOT affected").
- **`predictions.layer_scores`**, **`brain_decisions.observation`**, **`brain_decisions.action_logits`**, **`mode_change_log.gate_snapshot`**: all written via dedicated builders that pre-serialize. None of them are read back into in-memory state then re-passed to `insert_with_chain` (which is the round-trip pattern that triggers this bug).

The shadow worker's `list_open_positions` is the **only** read-back-then-rewrite path in production code for a chained-table column. No sister bugs found.

## Backfill for the 20 stuck positions

**No separate backfill needed.** The shadow worker's exit_monitor evaluates every open position on every candle tick. Once the fix is deployed:

1. Next candle eval triggers a close decision for any stuck position whose SL/TP/timeout fires.
2. `persist_closed_trade` now serializes the dict (via path A normalizing on read OR path B normalizing at the boundary) → INSERT succeeds → row lands in `shadow_trades` → `shadow_open_positions` row deleted → counter increments.

Stuck positions whose exit conditions don't fire (still inside the SL/TP band, no timeout) will continue to be evaluated cleanly each candle and will close when the market hits an exit condition — same as a healthy position.

## Files Changed

- `backend/app/db/payload_builders.py` — add `_normalize_mtf_directions_json` helper; apply in `build_shadow_trade_payload`.
- `backend/app/shadow/persistence.py` — import helper; apply in `list_open_positions` on the `mtf_directions_json` read-back.
- `backend/tests/db/test_payload_builders.py` — 8 new tests: helper unit tests (dict/str/none/empty/canonical-order) + builder integration tests (dict input serialises, str passes through, none stays none).
- `backend/tests/unit/test_shadow_persistence.py` — 2 new tests: `test_persist_closed_trade_when_pos_has_dict_mtf_directions` (regression for the prod bug) + `test_list_open_positions_normalises_dict_mtf_directions` (string round-trip).
- `docs/superpowers/specs/2026-05-23-pr-mtf-directions-json-serialization-fix.md` — this document.

## Local test results

- `pytest tests/db/test_payload_builders.py tests/unit/test_shadow_persistence.py -v` → 45/45 pass (35 + 10).
- Wider regression in flight.

## Post-deploy verification

- [ ] `docker logs tr-backend | grep "persist close failed"` returns zero matches in a 10-minute window after deploy.
- [ ] `docker logs tr-backend | grep "DataError"` returns zero matches for `argument $27` after deploy.
- [ ] `shadow_trades` row count strictly increases over the next candle close window (was stuck at 293).
- [ ] `shadow_open_positions` count decreases as stuck positions close.
- [ ] 2h soak: no new `DataError` rows; counter increments at a healthy rate.

## Risk surface

- **Read path coverage.** The helper is applied at the only known JSONB read-back site. If a future code path adds another reader of `shadow_open_positions.mtf_directions_json`, that reader must call `_normalize_mtf_directions_json` too. Defense-in-depth at `build_shadow_trade_payload` catches the case where it forgets.
- **Stable serialization for audit-chain compatibility.** Canonical form (`sort_keys=True, compact separators`) matches the writer hot-path. The `mtf_directions_json` column is in `NON_HASHED_ALLOW_LIST` per [`audit.py`](backend/app/db/audit.py) — it's a recording-only column today — so the canonical form is a hygiene win, not a correctness requirement for the audit chain.
- **No behavior change.** Trade entries continue to use unchanged code paths. The only thing that changes is that closes complete successfully when the position came from a DB-loaded source.
- **5/30 flip implication.** Live trades are independent of this bug and unaffected. **Not a 5/30 blocker.**
