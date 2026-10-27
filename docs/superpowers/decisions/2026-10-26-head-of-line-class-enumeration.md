# The head-of-line-block class, enumerated

**Date:** 2026-10-26
**Status:** swept, read-only. One instance found and fixed; the rest are
checked and clear, each with the reason stated.
**Order:** work-order item 3(b), 2026-10-26 — "one instance found means
enumerate, per this project's own fan-out rule."

---

## The signature is not the SQL shape

Grepping `ORDER BY ... ASC LIMIT N` is the obvious sweep and it is
**wrong in both directions**: it misses the real instance and flags
harmless reads. The actual signature has four parts, and all four are
required:

1. a **persisted pending marker** — so a failed item stays selectable
2. an **oldest-first ordering** — so it stays *first*
3. a **bounded batch** — so it can fill the batch
4. a **per-item skip that leaves the marker unchanged** — so failing is
   silent and repeatable

Drop any one and the block cannot form. That is why
`telegram_timeout.py` below is immune despite having (1), (2) and (4).

## The sweep

AST-based over **553 files**, reconstructing implicitly-concatenated
string literals — which matters, because a plain grep **missed
`validator.py`'s own query**: its SQL is split across adjacent Python
literals so `IS NULL` and `ORDER BY` land in different ones. A regex that
cannot see the real instance would have returned "class has no members"
and looked like a clean bill of health.

| file | verdict | why |
|---|---|---|
| `app/ml/validator.py` | **FULL SIGNATURE** | the instance. Fixed by migration 0045 + dead-letter. |
| `app/ops/telegram_timeout.py` | **DELETED 2026-10-26** | was clear of this class (pending marker + oldest-first + a skip, but **no `LIMIT`**, so it could never starve). Then found to have no producer and no caller at all and deleted — see `2026-10-26-telegram-timeout-deleted.md`. |
| `app/data/universe.py` | clear | `LIMIT 1` existence check, not a drain. |
| `scripts/probe_l9_null_corrected.py` | clear | report query. |

## The four the operator named

| worker | verdict | why |
|---|---|---|
| **`live_prediction_watermarks` advance** (`ws/futures_poll.py`) | clear | a **live-tail** poller: `limit=2`, documented "never backfills". The watermark advances only after the consumer finishes a candle (async-generator semantics), so a failed candle does not advance it — but the next poll fetches the **latest** bars, not the failed one, so nothing queues behind it. A skipped candle is logged at **ERROR** by a dedicated gap detector. Structurally immune, and already observable. |
| **shadow worker** (`shadow/worker.py`) | clear **of this class** | the `continue` paths sit in a seed loop over a **fixed symbol set** with no persisted pending marker; a failed seed is retried from scratch next cycle and the loop always completes over the whole set. **But see the separate finding below.** |
| **brain training** (`rl/replay_buffer.py`) | clear | `ORDER BY t.symbol, t.opened_at` with **no `LIMIT`** — loads the whole buffer each run. No cursor, no pending state. `rl/checkpoints.py` is `ORDER BY id DESC LIMIT 1`, newest-first single row. |
| **news ingest** (`news/ingest_worker.py`) | clear | per-source **time cursor** (`_last_fetch_ts`) over an external feed, held in module state, cold-starting at `now - BACKFILL_HOURS`. A fetch round returns a whole window and processes each item independently; an unparseable article is re-skipped but does not block newer ones. No bounded oldest-first batch. |

## A SEPARATE finding in the shadow worker — not this class, but the other one

`shadow/worker.py`'s seed path calls the **spot** kline client and
swallows 400/404 at **`log.debug`**:

```python
except httpx.HTTPStatusError as e:
    if e.response.status_code in (400, 404):
        log.debug("seed skip %s/%s (not on Binance Spot): %s", ...)
```

That is the **wrong-endpoint class** again — the one whose validator
instance just cost 53 days — at the quietest level the logging framework
offers, with the reason ("not on Binance Spot") written into the message.
It is a per-symbol degradation rather than a queue block, so it is not a
head-of-line instance and it is **not fixed here**. It was already on the
record as an unmigrated bar-fetch exemption; this sweep confirms it is
still open and still silent.

## What was shipped against the one instance

- `unvalidatable_reason` excludes dead-lettered rows from selection, so a
  hopeless row cannot hold the head. The attempt counter bounds how long
  it tries.
- The selector **keeps `ORDER BY target_ts ASC`**, deliberately.
  Chronological validation is correct behaviour and the ordering was
  never the bug; a mutant that flips it to `DESC` "fixes" the symptom by
  abandoning due-order and is caught by a structural test.
- `batch_limit` is **not** raised and the backlog is **not** manually
  drained, per the order.

## And the gap this sweep does not close

Enumeration finds the instances that exist **today**. The durable
protection is item 3(a)'s `(selected, advanced)` progress assertion,
which catches *any* worker that is alive and not progressing, including
one written next year in a shape nobody has thought of yet. See
`app/ops/check_events.py:record_queue_pass` and the test that
demonstrates a liveness-only check reading a stuck worker as healthy.
