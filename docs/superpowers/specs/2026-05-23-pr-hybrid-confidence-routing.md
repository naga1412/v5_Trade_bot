# PR-HYBRID-CONFIDENCE-ROUTING — confidence-tiered routing on telegram-approve mode

**Date:** 2026-05-23
**Branch:** `feat/pr-hybrid-confidence-routing`
**Base:** `dev`
**Class:** behavior-modifier on existing telegram-approve mode. NO new trading_mode value. NO schema migration. Dormant by default.

## Goal

Reduce manual approval burden on the operator: when the bot generates a HIGH-confidence signal under `users.trading_mode='telegram-approve'`, auto-execute it directly via Binance instead of waiting for a Telegram approval tap. MEDIUM-confidence signals still route to Telegram approval (existing behavior).

Operator outcome: **sleep more while bot handles top-decile trades.**

## Design — OPTION 2 (parameter-driven, no mode change)

The original prompt proposed adding `trading_mode='hybrid'` as a 4th mode value with two new thresholds. **On code-aware critique, OPTION 2 ships the same UX with a single Setting and a single dispatcher branch — significantly less scope and lower risk.**

### Rejected: OPTION 1 (new `'hybrid'` mode)

Would have required:

- **New alembic migration** to DROP + recreate the `users.trading_mode CHECK (trading_mode IN ('manual', 'telegram-approve', 'fully-auto'))` constraint at [migration `0004_users_and_invitations.py:27-28`](backend/alembic/versions/2026_05_04_0004_users_and_invitations.py#L27-L28).
- **`modes.py` updates**: `Mode` Literal at line 30, `_MODE_RANK` at line 34, and new upgrade-path gate semantics.
- **`promotion.py` updates**: `TargetMode` Literal at [`promotion.py:42`](backend/app/trading/promotion.py#L42), new threshold tuple, new `_check_thresholds` branch.
- **Frontend mode selector** update for the dashboard.
- **Audit-trail expansion** (mode_change_log distinguishes hybrid).

That's ~10 files touched + a schema migration. Rejected because OPTION 2 delivers the same UX with much less surface area.

### Accepted: OPTION 2 (one Setting, one branch)

**One new Setting** in [`backend/app/config.py`](backend/app/config.py):

```python
HYBRID_AUTO_SCORE_THRESHOLD: float | None = None
```

**One new branch** inside [`backend/app/trading/execution/dispatcher.py`](backend/app/trading/execution/dispatcher.py) `dispatch()`, INSIDE the existing `current_mode == "telegram-approve"` block:

```python
if current_mode == "telegram-approve":
    _hybrid_threshold = get_settings().HYBRID_AUTO_SCORE_THRESHOLD
    if (
        _hybrid_threshold is not None
        and proposal.entry_score is not None
        and abs(proposal.entry_score) >= _hybrid_threshold
    ):
        log.info("hybrid_routing: ... -> _place_live_order")
        order_id, sig_id = await _place_live_order(...)
        return DispatchResult(outcome="placed_hybrid", ...)
    # else: fall through to existing _send_telegram_signal path
```

**That's it.** No mode change, no migration, no promotion.py update, no frontend change. The per-trade routing decision is fully captured by the dispatch outcome:

- `placed_hybrid` — auto-executed via hybrid routing (NEW)
- `sent_telegram` — Telegram approval (existing)
- `placed` — fully-auto mode (existing)
- `emitted` — manual mode (existing)

Audit-trail purity: each dispatch produces a log line + a `live_trades` or `telegram_signals` row, so per-trade routing is fully auditable.

## Lower bound (entry-quality filter)

The existing entry-quality gate at [`backend/app/core/gates/entry_quality.py`](backend/app/core/gates/entry_quality.py) is the "lower bound" — signals below `MIN_ENTRY_SCORE_LONG` (for LONGs) are filtered out before reaching the routing branch, and `DISABLE_SHORT_SIGNALS` blocks every SHORT signal. **No additional `MIN_ENTRY_SCORE_TELEGRAM` setting is needed** — the existing gate IS the lower bound.

`HYBRID_AUTO_SCORE_THRESHOLD` is the **upper-tier** auto-execute threshold. Signals in `[MIN_ENTRY_SCORE_LONG, HYBRID_AUTO_SCORE_THRESHOLD)` continue to route to Telegram. Signals at or above `HYBRID_AUTO_SCORE_THRESHOLD` auto-execute.

## Safety profile

- **DISABLE_SHORT_SIGNALS still applies on both paths.** The block fires at the entry-quality gate (line 653 of dispatcher.py), BEFORE the routing branch. SHORTs are blocked regardless of routing destination. Verified by `test_hybrid_routing_short_blocked_by_disable_short_signals`.
- **All pre-conditions still execute regardless of routing.** Allowlist (PR10), entry-quality (PR-strategy-1), funding-rate guard, cooldown (PR8), MTF gates (PR2), max-positions — all fire before the routing branch.
- **No-score signals fall through to Telegram.** A `SignalProposal` without `entry_score` (admin manual test trade, older code path) cannot accidentally auto-execute. Verified by `test_hybrid_routing_no_score_falls_through_to_telegram`.
- **fully-auto and manual modes unaffected.** The new branch lives strictly inside the telegram-approve mode path. Verified by `test_hybrid_routing_does_not_affect_fully_auto_mode` and `test_hybrid_routing_does_not_affect_manual_mode`.
- **abs(score) semantics for the threshold.** High-conviction SHORTs (-0.60) trigger the same auto-execute as high-conviction LONGs (+0.60) — as long as DISABLE_SHORT_SIGNALS isn't set. Verified by `test_hybrid_routing_short_signal_above_threshold_auto_executes`.
- **Inclusive boundary (`>=`).** `abs(score) == threshold` qualifies for auto-execute. Verified by `test_hybrid_routing_exact_threshold_boundary`.

## Operator activation flow

1. **Default state:** `HYBRID_AUTO_SCORE_THRESHOLD` is unset (`None`). Hybrid routing is dormant. Every telegram-approve signal still goes through the Telegram approval handshake.
2. **To enable:** edit `/opt/trading-radar/.env` on the Hetzner host, add `HYBRID_AUTO_SCORE_THRESHOLD=0.45`, then `docker compose up -d backend` (recreate the backend container — `lru_cache` on `get_settings()` requires a process restart per the PR2 rollback runbook).
3. **To disable:** remove the line or set `HYBRID_AUTO_SCORE_THRESHOLD=` (empty), then recreate the backend.
4. **Threshold tuning:** start at `0.45` (conservative). Adjust based on observed dispatch distribution. Current score distribution (per PR-TRADE-RATE-DIAGNOSTIC) is `±0.02` to `±0.24` for most signals, with rare excursions above `±0.30`. At `0.45`, **zero auto-trades will fire** in current market conditions — that's the desired safety margin for first deploy. Raise/lower as confidence grows.

## Files Changed

- [`backend/app/config.py`](backend/app/config.py): add `HYBRID_AUTO_SCORE_THRESHOLD: float | None = None` Setting with docstring explaining the activation model.
- [`backend/app/trading/execution/dispatcher.py`](backend/app/trading/execution/dispatcher.py): insert hybrid-routing branch inside the existing telegram-approve block. ~30 LoC including comments.
- [`backend/tests/unit/test_dispatcher_e2e.py`](backend/tests/unit/test_dispatcher_e2e.py): append 8 new TDD tests (and 1 helper) covering dormant default, below-threshold, at/above-threshold, exact boundary, SHORT auto-execute, SHORT blocked by DISABLE_SHORT_SIGNALS, no-score fallthrough, fully-auto unaffected, manual unaffected.
- [`docs/superpowers/specs/2026-05-23-pr-hybrid-confidence-routing.md`](docs/superpowers/specs/2026-05-23-pr-hybrid-confidence-routing.md): this document.

## Test results

`pytest tests/unit/test_dispatcher_e2e.py -v` → **17/17 pass** (8 new + 9 existing). Broader dispatcher test surface (`tests/unit/test_trading_dispatcher.py` + `tests/trading/`) all green.

## Acceptance — post-deploy

- [ ] Default behavior unchanged: with `HYBRID_AUTO_SCORE_THRESHOLD` unset, every telegram-approve dispatch produces `outcome="sent_telegram"`. Confirm via `docker logs tr-backend | grep -c "sent_telegram"`.
- [ ] No new "hybrid_routing" log lines until operator sets the threshold.
- [ ] After operator sets the threshold + restart: hybrid_routing log lines appear for high-confidence signals (`|score| >= 0.45`), Telegram messages continue for medium-confidence signals.
- [ ] No regression in dispatch denial counters: `gate_denial_total{reason=...}` increments identically pre/post-deploy on dormant default.

## Out of scope (deferred to follow-ups)

- **Per-user threshold** (`users.hybrid_auto_score_threshold` column). Currently a global env var. If the bot ever serves >1 operator, this needs to become per-user.
- **Confidence dimension on the threshold.** Currently only score-magnitude gates. A future PR could add `HYBRID_AUTO_CONFIDENCE_THRESHOLD` so the operator can require BOTH high-score AND high-confidence for auto-execute. Score-only matches the existing entry-quality gate's semantics, so this PR stays score-only.
- **Full `'hybrid'` mode (OPTION 1)** — if the operator later wants the audit-trail clarity of a distinct mode value, OPTION 1 becomes a clean follow-up. OPTION 2 doesn't paint into a corner.
- **Frontend "auto-route at threshold" indicator** on the dashboard. The dispatch outcome already distinguishes routing decisions; surfacing this in the UI is a separate UX change.

## Risk surface

- **Tiny.** 1 Setting field, 1 dispatcher branch, ~30 LoC source code change. No schema migration. No backwards-incompatible changes.
- **Operator's existing safety mechanisms all remain in force**: kill switches, allowlist, entry-quality, funding guard, MTF gates, max-positions, dispatch denial counters.
- **Reversibility:** unset the env var + recreate backend. One operator action, no DB writes required.
- **5/30 flip:** **NOT a blocker.** Default behavior unchanged; operator opts-in explicitly. The operator's 5/30 flip is `AUTONOMOUS_TRADING_ENABLED=True` + `trading_mode='fully-auto'`. This PR is orthogonal — only modifies `telegram-approve` mode behavior.
