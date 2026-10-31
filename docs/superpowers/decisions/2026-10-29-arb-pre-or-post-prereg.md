# ARB's ConnectTimeouts — pre-registration, written before the read

**Status: pre-registration.** Committed before the query is run. The
branch conditions below are the operator's, quoted from the 2026-10-29
work order; nothing here is chosen after seeing a number.

## The question

`2026-10-29-mtf-status-code-read.md` left ARBUSDT unexplained. It now has
a *mechanism* — `ConnectTimeout` on all six timeframes — but only 2
occurrences in 47 cycles (4.3%), which cannot produce the persistent
zero-agreement the symbol-level counter recorded across two consecutive
cycles. Either the mechanism is the whole story and the counter read
differently than I think, or something else is also acting.

#617 shipped defect (b): a shared, pooled, weight-limited client in place
of `httpx.AsyncClient(...)` constructed per call. The hypothesis it
embodies, written out in `mtf_confluence.py:65-87`, is **socket churn** —
a fresh client per call never reuses a connection, so TIME_WAIT / FD
accumulation produces transport failures on arbitrary symbols. That is
the thing a pre/post split can test.

So: **were ARB's two ConnectTimeouts before or after the pooling fix went
live?**

## The boundary instant

#617 is `8668f80`. Its deploy (run `8668f80c`) started
**2026-10-29T16:23:53Z** and completed **2026-10-29T16:28:28Z**.

The conservative boundary is the **completion** stamp, 16:28:28Z: the
container is only certainly running the new image after the deploy
finishes. 16:23:53Z is the earliest instant at which the new code
*could* have been running. A stamp between the two is indeterminate and
will be reported as such rather than assigned to a side.

## The discriminator, and why it is sound

`check_events` rows with `check_name='mtf_kline_fetch_failed_detail'` and
`subject` of the form `<SYMBOL>/<tf>/<kind>`, unique per
`(check_name, subject, bucket_date)`.

- `last_seen_at` **before** the boundary ⇒ no occurrence after it.
- `last_seen_at` **after** the boundary ⇒ at least one occurrence after
  it, because an existing daily row is UPDATED in place and its
  `last_seen_at` advances.

Two properties make this hold rather than merely look like it holds:

1. `flush_mtf_fetch_failures` writes a row only when the delta since the
   previous flush is positive. A flush with nothing new writes no detail
   row at all, so a row's `last_seen_at` marks a flush that **carried a
   real increment**.
2. The counters and their `_last_flushed_detail` mirror are module
   memory, so the deploy's container restart zeroes both together. A
   post-restart occurrence therefore presents as a fresh positive delta
   and *must* advance `last_seen_at`.

**Known limitation, stated up front:** `record_daily` stamps
`now()` at flush time, shared by every row in one flush, so these
timestamps resolve the *flush*, not the occurrence — to within one
5-minute flush interval. This is enough to place a stamp on one side of
16:28:28Z only when it is more than 5 minutes clear of it. A stamp
within 5 minutes of the boundary is indeterminate and will be reported
that way.

## σ-provenance

**σ-provenance:** none imported, and none needed — deliberately. This is
not a mean comparison. The prediction below is **exactly zero**, so the
falsification threshold is **one occurrence**, a count, not a multiple of
a dispersion. No σ from any other population enters this test, which is
the whole point of recording the field.

## The branches — the operator's, verbatim in effect

| Read | Verdict | What is pre-registered |
|---|---|---|
| **BOTH stamps predate 16:28:28Z** | 1(b) is **untested** against them | The 31 Oct read predicts **ZERO** `ConnectTimeout` occurrences for ARBUSDT. **A non-zero count falsifies pooling** as ARB's explanation. |
| **EITHER stamp postdates 16:23:53Z** | pooling is **excluded** | The hypothesis space narrows to **host-level networking**: FD limits, ephemeral-port exhaustion, DNS, the Hetzner NIC. To be said plainly, not left open-ended. |

## The 31 Oct read, specified now

Only fires on the BOTH-PREDATE branch.

- **No earlier than 2026-10-31T16:30Z**, giving 47+ post-fix cycles to
  match this window's denominator.
- **Query:** the same `check_events` read, `bucket_date` from 2026-10-29
  onward, `subject LIKE '%/ConnectTimeout'`.
- **Statistic:** `ConnectTimeout` occurrences for ARBUSDT, summed over
  `increment`, with the cycle count as the denominator.
- **Decision:** `0` confirms pooling. `>= 1` falsifies it and moves the
  hypothesis to host-level networking — the same destination as the
  EITHER-POSTDATES branch, reached two days later.

A falsification does **not** get reinterpreted as "pooling helped but did
not fully fix it". The prediction is zero because the mechanism claims to
remove the cause, not to reduce it.
