# Telegram signal dedup — measurement + scope

**Class:** read-only measurement + a scope guarantee for `TELEGRAM_DEDUP_COOLDOWN_HOURS`. **Do not amend by squash.** Any future change to this decision requires a new decision record replayed in order.

## Why this exists

2026-09-03's checkpoint found the established-cohort signal count had
jumped ~4x with no bug in signal generation — the real mechanism is
that the only existing cooldown (`LIVE_COOLDOWN_ENABLED`, gated on a
`live_trades` row actually *closing*) has never fired once in the
account's history (`live_cooldowns` has zero rows, ever — confirmed
directly against prod). A persistent single-symbol setup can and does
re-fire a Telegram card on every qualifying candle. This is the direct
answer to "quality over quantity": removing the noise the operator has
to wade through, without touching signal generation or ranking (which
the operator's own prior finding — entry_score correlates with realized
P&L at ~0.33σ — rules out as a viable lever).

## A1 — read-only replay measurement

Simulated four candidate per-(symbol, direction) cooldown durations
against the real `telegram_signals` history for the trailing 14 days
(566 rows, 2026-08-20 21:00 UTC through 2026-09-03 20:01 UTC, pulled
directly from prod). Semantics: a signal is SENT if no prior signal for
that (symbol, direction) was sent within the cooldown window (or this is
the first ever); SUPPRESSED otherwise. A suppressed signal does not
reset the cooldown clock — only an actually-sent one does, matching
`live_cooldowns`' own "starts from a real event" semantics rather than
resetting on every attempt.

**14-day totals:**

| Cooldown | Total | Suppressed | % suppressed | Would-be count |
|---|---|---|---|---|
| 2h | 566 | 192 | 33.9% | 374 |
| 6h | 566 | 321 | 56.7% | 245 |
| 12h | 566 | 360 | 63.6% | 206 |
| 24h | 566 | 403 | 71.2% | 163 |

**2026-09-03 specifically (the day that triggered this investigation,
198 raw signals by 20:01 UTC, day not complete):**

| Cooldown | Raw | Suppressed | Would-be | Worst symbol before | Worst symbol after |
|---|---|---|---|---|---|
| 2h | 198 | 71 (35.9%) | 127 | LTC/USDT=15 | LTC/USDT=7 |
| 6h | 198 | 123 (62.1%) | 75 | LTC/USDT=15 | USELESS/USDT=3 |
| 12h | 198 | 135 (68.2%) | 63 | LTC/USDT=15 | SEI/USDT=2 |
| 24h | 198 | 147 (74.2%) | 51 | LTC/USDT=15 | SEI/USDT=1 |

The shape is consistent across the full 14-day window, not just the
elevated day: the "worst symbol" count collapses sharply at every
duration tested, and even a 2h cooldown removes a third of all traffic.
By 6h, no single symbol clears more than 3-4 sends in a day anywhere in
the 14-day sample. Full per-day breakdown (all 4 durations × 15 days)
is in this session's own working notes; the totals and the worst-day
detail above are the numbers that matter for picking a value — request
the full table if a finer read is wanted before choosing.

**No duration was picked by this measurement.** That's an operator
decision — this table exists so it's a choice between numbers, not a
guess.

## A2/A3 — implementation, and the non-negotiable scope guarantee

`TELEGRAM_DEDUP_COOLDOWN_HOURS: float | None` (`app/config.py`, default
`None` = disabled, byte-identical to today's behavior when unset).
Enforcement lives in one new function, `_check_telegram_dedup`
(`app/trading/execution/telegram_dedup_gate.py`), called from exactly
one place: `dispatcher.dispatch()`'s `telegram-approve` branch,
immediately before `_send_telegram_signal` — after every other gate
(entry-quality, funding, PR8 cooldown, PR2 MTF/SHORT) has already run
and passed, and after the hybrid-auto-execute check (which routes to
live order placement, not a Telegram card, and is unaffected by this
setting either way).

**Where the check sits, precisely**: `dispatch()` is the live-trading
dispatch function, reached only via `_maybe_dispatch` →
`dispatch_if_eligible` → `dispatch()` from the live prediction path
(`app/ws/live_prediction.py`). Shadow's own trade lifecycle
(`app/shadow/worker.py`) never calls `dispatch()` or anything in
`app/trading/execution/` — it has its own independent evaluator, gate,
and persistence path entirely inside the `app/shadow/` package. A
suppressed Telegram card changes nothing upstream: the signal was still
fully generated, fully gated, and (on the shadow side, an entirely
separate lane) still evaluated and — if it qualifies — still opened and
recorded exactly as it would with this setting unset.

**Proof, not just assertion** — enforced in CI going forward via
`backend/tests/trading/test_telegram_dedup_scope.py`:

1. Every `.py` file under `app/shadow/` (the full package, plus
   `worker.py` and `breakeven_variant.py` checked again by name for
   extra certainty) is swept for any reference — import, call, string,
   comment — to the dedup gate, its check function, or its settings
   key. Zero references found; the test fails loudly if a future change
   ever introduces one.
2. `_check_telegram_dedup` is called from exactly one file in the
   entire `app/` tree: `dispatcher.py`. A second call site anywhere
   would mean the gate is being reused outside its designed scope.
3. An AST-level ordering check confirms the gate call sits textually
   between the entry-quality gate and the actual send call inside
   `dispatch()` — not hoisted earlier (which could skip other real
   gates) or placed after the send (which would defeat the point).

**A4 — keyed on (symbol, direction), not symbol alone**: the SQL query
inside `_check_telegram_dedup` filters on both columns
(`WHERE symbol = :s AND direction = :d`); a direction reversal is
treated as new information and is never suppressed by the opposite
direction's recent send, regardless of cooldown duration. Covered by
`test_gate_keyed_on_direction_reversal_recent_opposite_direction_still_sends`.

**Fail-open**: any DB error on the dedup read returns None (let the
send proceed) — matching `cooldown_gate.py`'s own established
philosophy exactly. A dedup gate that failed closed would silently drop
real signals on a transient DB blip, which is worse than an occasional
duplicate.

## Status

Built, CI-green, held per the operator's explicit "build parameterized,
do not ship" instruction. `TELEGRAM_DEDUP_COOLDOWN_HOURS` stays unset
(dedup disabled, no behavior change) until the operator picks a
duration from §A1's table.
