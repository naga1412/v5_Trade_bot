# The prediction_validations backlog — diagnosed, not fixed

**Date:** 2026-10-26
**Status:** diagnosis only, as ordered. No `batch_limit` change, no manual
drain, no healer change, no DB write.
**Order:** work-order item 1, 2026-10-26.

---

## The answer in one line

**Validation stopped on 2026-09-03 and stayed stopped for 53 days.** It is
a **head-of-line block**, not a capacity problem — which is why the
operator's arithmetic correctly excluded the capacity explanation before
anyone looked.

## 1. The two reads, an hour apart

| | time (UTC) | due-but-unvalidated |
|---|---|---|
| **T0** | 2026-10-26 16:46:33 | **94,053** |
| **T1** | 2026-10-26 17:46:35 | *(see §6)* |

## 2. WHEN it started: 2026-09-03

**It is not mine. It predates #609 by 53 days.**

The decisive statistic is the **lag** — `validated_at - target_ts` —
grouped by completion day, not by due date:

| completion day | rows validated | due-dates covered | **avg lag** |
|---|---|---|---|
| … 2026-07-29 → 2026-09-02 | ~480–1,338/day | that same day | **0.0 h** |
| **2026-09-03** | **342** | 09-03 | **0.1 h** |
| *(nothing at all for 21 days)* | — | — | — |
| 2026-09-24 | 185 | 09-02 → 09-03 | 508.9 h |
| *(nothing for 32 days)* | — | — | — |
| **2026-10-26** | **8,600** | 09-02 → 09-08 | **1,221.4 h** |

A lag of 0.0 h every day for weeks means it was validating in the same
hour the bar closed. Then on 2026-09-03 it did 342 rows (against a
normal ~1,600/day) and stopped.

### I nearly reported the wrong date, and the method is the lesson

My first query grouped by **due date** and asked what fraction was
complete. It read: 100% every day through 2026-09-07, **16.2%** on 09-08,
then **0.0% for 48 consecutive days**. That is a clean step function and
it looks exactly like an outage beginning 2026-09-08.

It was the **drain frontier**, not the onset. A second query by
completion hour showed work had restarted ~3 h earlier and was walking
the queue oldest-first: 14:00 covered due-dates 09-02→09-03, 15:00 did
09-03→09-06, 16:00 did 09-06→09-08. 2026-09-08 was simply where the drain
had **got to** at the moment I looked.

**A one-shot per-due-date coverage table cannot distinguish "stopped on
date X" from "currently draining through date X".** Both produce a step
function at X. The operator's instruction to read twice an hour apart is
what caught this, and it caught it before the write-up left my hands.
Filed as a standing rule; sibling of the cannot-fail family in that the
query was correct and the numbers were true — the statistic just could
not answer the question asked of it.

## 3. WHY it stopped: a head-of-line block

`app/ml/validator.py`'s selector:

```sql
SELECT id, symbol, timeframe, direction, anchor_close, target_ts
FROM prediction_validations
WHERE validated_at IS NULL AND target_ts <= :now
ORDER BY target_ts ASC LIMIT :lim        -- lim = batch_limit = 100
```

**Oldest-first.** And `_validate_one` returns **`False`** — a silent skip,
not an exception — whenever `_fetch_close_at` yields `None`.

So if the oldest 100 due rows cannot be validated, every pass selects
**the same 100 rows**, skips all 100 silently, commits nothing, and never
reaches row 101. The queue is blocked permanently by its own head.

### This is why more drain cannot help

Drain capacity is 100 per 60 s = 6,000/hr against a fill of ~1,500–2,300
rows/day ≈ 60–95/hr — **12–40x over-provisioned**, exactly as the order
states. Raising `batch_limit` **cannot clear this**: the blocking rows are
by construction always first in the ordering, so a larger batch re-reads
the same poisoned head plus more rows behind it. A 12–40x
over-provisioned drain that is not draining is not short of drain.

### The observability gap that cost 53 days

`validate_pending` returns `updated`, but **no pass ever reports
"selected 100, advanced 0"**. A skip and a nothing-to-do look identical
from outside: both produce `updated = 0` and no log line. Fifty-three
days of total non-validation generated no alarm of any kind. This is the
same family as the cannot-fail rule — the condition that distinguishes
healthy from dead was never computed, so it could never be observed.

## 4. What restarted it today

Validation resumed in the **14:00 UTC hour** on 2026-10-26, after 53 days
of silence. Two deploys land in that window: **#610 at 14:06:37Z** and
**#609 at 14:42:50Z**. Both restart the backend.

**I am not attributing the recovery to either one.** Two mechanisms are
consistent with the timing and they are distinguishable:

- **#609's market routing** replaced `BinanceClient.fetch_klines` (spot
  for every symbol) with market-resolved fetching, so a futures-only
  symbol now reaches `fapi` instead of returning nothing from spot. If
  un-validatable futures-only rows were the poisoned head, this is a
  real fix.
- **A bare restart** could clear a transient blockage without fixing
  anything, in which case the block returns when the frontier reaches
  whatever rows caused it.

**The discriminator is free and already running:** if the drain walks
cleanly past 2026-09-09 into the futures-heavy period and keeps going,
the routing fix is doing it. If it stalls at a date, the restart merely
bought a partial drain and the defect is still live. §6 reports which.

One thing ruled out: the head of the queue at 16:50 was **BTC/USDT,
ETH/USDT, ADA/USDT, DOGE/USDT, BNB/USDT** — mainstream spot-listed names.
That is **not** evidence about the original blockage, because those rows
are merely queued behind an advancing frontier rather than blocked. I
note it because a composition read taken mid-drain invites exactly that
misreading.

## 5. The volume step that set this up

`prediction_validations` row volume per due-day:

- through 2026-08-30: **~480/day** (20 symbols x 24 h)
- **2026-08-31: 668** — the ramp begins
- **2026-09-01: 1,320**, 09-02: 1,338, 09-03: **1,621**

The futures expansion roughly tripled the population in the three days
immediately before the stop. That is consistent with new,
differently-shaped symbols entering an oldest-first queue whose per-row
failure mode was a silent skip, but **consistency is not proof** and I did
not establish which specific rows formed the poisoned head — the drain
had already passed them by the time I looked.

## 6. The delta, and which case it is

*(T1 read at 2026-10-26 17:46:35Z — filled in below.)*

The order named three cases:

- **falling ~5,500/hr** → historical accumulation draining, benign
- **flat** → not committing; `batch_limit` irrelevant, defect elsewhere
- **growing** → neither; report immediately

<!-- T1-RESULT -->

## 7. What was deliberately NOT done

- `batch_limit` **not** raised. §3 explains why it would not help.
- Backlog **not** manually drained. No DB write of any kind.
- Healer **not** touched.
- No fix shipped for the head-of-line block itself. The fix shape is an
  attempt counter or dead-letter path so a repeatedly-failing row stops
  blocking the rows behind it — **not** more throughput. That is a
  behaviour change to a live worker and is left for explicit direction.

## 8. Consequence for the rest of this work order

**Item 2's outcome arm has no data because of this outage.** Every row in
the 30-day `futures_poll` bar-clearing cohort has a validation row and
**zero** carry a `validated_at` (213 rows, 213 validation rows, 0
outcomes). The pre-registration's step-0 abort fires: **data-absent, not
underpowered.** Any realized-outcome study over any window after
2026-09-03 is affected until the drain completes.
