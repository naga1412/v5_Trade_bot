# Follow-up findings — the partition, the attribution, and one worker that does not exist

**Date:** 2026-10-26
**As-of:** prod clock 2026-10-26T18:50Z
**Status:** measured, read-only except where a PR is named.

---

## 1. The mtf counter partition — the operator's arithmetic was right

20 × 6 = 120 but count = 111. **The deficit of 9 is real and it resolves
exactly.** I had reported "every symbol at exactly 6" from a truncated
six-row display; the full list is:

| bucket | n symbols | count each | subtotal |
|---|---|---|---|
| 6 of 6 timeframes failed | **17** | 6 | 102 |
| **3 of 6 failed** | **3** | 3 | 9 |
| | | | **111** ✓ |

The three partial failures are **AAVEUSDT, BNBUSDT, GTCUSDT**.

### Partition, per the rule

- **count < 6 ⇒ TRANSIENT. Confirmed: AAVE, BNB, GTC.** All three are
  spot-listed majors that failed *some* timeframes and succeeded on
  others in the same cycle. A listing problem cannot be partial — the
  symbol either has a spot pair or it does not — so a 3-of-6 split is
  positive evidence of a per-request failure, consistent with the 2.0s
  `TF_FETCH_TIMEOUT_S`.
- **count = 6 ⇒ still ambiguous. 17 symbols.** Within them,
  `1000PEPEUSDT` and `1000SHIBUSDT` are futures-only on independent
  grounds (the `1000x` prefix is a futures contract convention), so those
  are permanent. The rest need the second cycle.

**CORRECTION TO MY OWN FLAG:** I named "ETC/ARB" as the puzzle. They are
at **6**, not <6, so they are **not** in the confirmed-transient set —
they remain ambiguous. The confirmed-transient set is AAVE/BNB/GTC.

### The second cycle is not available yet, and that is expected

All 20 rows share `first_seen_at == last_seen_at == 18:05:24`, and
`check_events` has stood at 22 rows for 45 minutes. The counter is
per-scoring-cycle and cycles are **hourly**, so the next data point
arrives at ~19:05Z. Nothing is wrong with the flusher — its heartbeat row
advanced to 18:31:44 on schedule, which is exactly the distinction
`record_daily(increment=0)` exists to make.

**The 2.0s timeout is NOT raised.** Transience is established for three
symbols; that it is a *timeout* specifically is not, and the next cycle
costs nothing.

## 2. The veto's real non-coverage — fleet-wide, not cohort-restricted

`dispatch_decisions` ⋈ `predictions` on (symbol, timeframe, ts),
`outcome='sent_telegram'`, `timeframe='1h'`, 30 days to
2026-10-26T18:40Z:

| | |
|---|---|
| dispatched cards | **780** |
| rows that failed to join | **0** |
| **joined, `mtf_agreement IS NULL`** | **121 (15.5%)** |
| joined, mtf present | 659 |
| distinct symbols | **135** |

**121 of 780 delivered cards currently pass the mtf veto by fail-open.**
That is the veto's actual non-coverage and the de-vetoed experiment's
actual population.

The zero-unjoined count matters: a `LEFT JOIN` whose misses silently
counted as NULL would be a join-that-cannot-fail, so the three cases are
separated explicitly and the "no join" column reads 0.

**This is ~11× the futures_poll-only picture** (which had 188 NULL
crossing-layer rows over 22 symbols). The flip screen measured one cohort
at the crossing layer; this measures the whole fleet at the dispatch
layer. Not comparable, and the larger number is the one that describes
the operator's exposure.

## 3. The recovery attribution — DECISIVE, and it is #609

The competing explanation was that #610's 14:06Z restart refreshed a
stale listing cache, under which #609 is not the fix and the bug recurs
whenever the cache goes stale.

**Eliminated. There are no validations before #609 landed.**

Partitioning validations in 14:00–15:30Z at #609's 14:42:50Z merge:

| window | rows | first `validated_at` |
|---|---|---|
| **before 14:42:50Z** | **0** | — |
| after | 3,200 | **14:44:27** |

The first validation of the entire recovery is **14:44:27**, ~95 s after
#609's merge and consistent with the deploy's container restart. #610
restarted the backend at 14:06Z and **36 minutes of nothing followed.**

Of those 3,200, **263 are the six poison symbols**, target_ts spanning
09-02 18:00 → 09-05 02:00 — the formerly-unvalidatable rows becoming
validatable immediately after the routing fix.

### And the measurement lesson repeats

My "recovery resumed in the 14:00 hour" came from an **hour-truncated**
histogram (`DATE_TRUNC('hour', validated_at)`), which lumps 14:00–14:59
into one bucket and so cannot resolve a 36-minute gap. Reading raw
`MIN(validated_at)` settles it instantly. Same family as the
moving-frontier trap: **an aggregation coarser than the event being
attributed manufactures ambiguity that the raw timestamps do not have.**

Weighed against the operator's point that the 2026-10-10 restart did not
clear the block, the two agree: restarts do not fix this, routing does.
**The class is closed.**

## 4. telegram_timeout.py — NOT wired, and the reason is substantive

**`expire_pending_approvals` has no production caller.** Verified four
ways:

- absent from `app/ops/worker_registry.py` (its line 232 is the Telegram
  *poller*, a different worker)
- absent from `app/main.py`
- no script, cron or workflow invokes it
- its only references anywhere are its own module and its tests

And the feature is inert on the **write** side too: `make_pending_notes`
has no callers outside the module, and prod carries **90
`rl_checkpoints` rows with 0 pending-approval markers.**

So the approval-timeout drain does not run, over a marker nothing writes,
against zero rows. **Blast radius: zero.**

**Therefore the 3(a) emitter was not wired there.** An emitter on a loop
that never executes writes nothing, and registering it in
`EXPECTED_CHECKS` would report it **SILENT forever** — the standing
false alarm that `check_events`' own registry docstring exists to
prevent ("twenty standing false alarms is how an alarm channel gets
ignored"). The constraint the operator set — never propagate into the
approval path — is satisfied trivially by a path that does not run, which
is a sign the instruction was aimed at a worker that isn't there rather
than that the constraint was easy.

This is the `update_pattern_stats` shape again: a function with passing
unit tests, wired to nothing, invisible because the tests pass. **The
finding is the dead worker, not the missing emitter.** Wiring the worker
is a behaviour change to the brain-approval path and is not done here.

## 5. The outcome arm — step 0 fires again, and the reason is measurable

Run under the **unrevised** pre-registration. Its step 0 is: count cohort
rows with a non-NULL `validated_at`; if ~zero, abandon and report
data-absent.

| | |
|---|---|
| cohort rows | **212** |
| **with an outcome** | **0** |
| cohort window | 2026-09-27 04:00 → 2026-10-26 14:00 |
| **drain frontier** | **2026-09-12 04:00** |

**The frontier is 15 days of due-time short of the cohort's first row.**
This is not "still unresolvable" — it is the same data-absent abort,
with a measured distance to the trigger rather than a guess.

Frontier velocity, from three reads: 09-10 08:00 (17:46:55Z) → 09-11
19:00 (18:27:36Z) → 09-12 04:00 (18:36:36Z) ≈ **57–60 h of due-time per
hour of wall-clock**. So:

- **first** cohort row gets an outcome at ~**+6.0 h** (≈ 00:40Z)
- **last** cohort row at ~**+17.9 h** (≈ 12:30Z)

The pre-registration's power analysis is unchanged and unrevised: at the
expected ~30/~158 split the SE is **≈1.0 pp** against decision bars of
0.2–0.3 pp, so the comparison is **expected to be unresolvable even once
the data exists**, and a sub-bar difference will not be reported as
directional.
