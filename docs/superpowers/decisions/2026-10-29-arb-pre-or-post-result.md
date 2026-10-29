# ARB's ConnectTimeouts — the read. POOLING IS EXCLUDED.

Executes `2026-10-29-arb-pre-or-post-prereg.md`, committed at `09d7afc`
before this query ran.

## Verdict: the EITHER-POSTDATES branch. Pooling is excluded.

**ARBUSDT took a `ConnectTimeout` on all six timeframes at
2026-10-29T18:03:44Z** — 1h 35m after #617's deploy completed at
16:28:28Z.

| subject | inc | bucket | last_seen_at | side |
|---|---|---|---|---|
| `ARBUSDT/{5m,15m,1h,4h,1d,1w}/ConnectTimeout` | 1 each | 2026-10-27 | 2026-10-27 17:04:55Z | PRE |
| `ARBUSDT/{5m,15m,1h,4h,1d,1w}/ConnectTimeout` | 1 each | 2026-10-28 | 2026-10-28 04:00:36Z | PRE |
| `ARBUSDT/{5m,15m,1h,4h,1d,1w}/ConnectTimeout` | 1 each | 2026-10-29 | **2026-10-29 18:03:44Z** | **POST** |

Three things make this conclusive rather than inferential:

1. `cumulative_since_process_start = 1` on the 18:03:44Z rows. Had the
   process been running since 10-27, ARB's 15m counter would read 3. It
   reads 1, so the process restarted between 10-28 04:00Z and
   18:03:44Z — independently corroborating the 16:28:28Z deploy restart
   from the counter itself.
2. The 16:29:15Z in-container read-back already confirmed the **running
   object** was the pooled client: `RateLimitedClient`,
   `_max_connections == 20`, `klines` weight 2. So the code live at
   18:03:44Z is known to be pooled, not assumed to be.
3. 18:03:44Z is the **only** flush since the deploy that carried any
   increment at all (`min(last_seen_at) = max(last_seen_at)` across
   every post-boundary row). There is no ambiguity about which flush
   this was.

Per the pre-registration: **the hypothesis space narrows to host-level
networking — FD limits, ephemeral-port exhaustion, DNS, the Hetzner
NIC.** Said plainly, as instructed, rather than left open-ended.

## The rule had a soft edge. Second instance.

The operator's rule was written over "**both** ConnectTimeout
occurrences" — the two that existed when it was written. The read found
**three**: a new one landed at 18:03:44Z, between the rule being written
and the query being run. The rule's input set was not closed, because
the counter it reads is still live.

The outcome is unambiguous anyway, and in a way worth recording: **both
branches converge.** Taken literally, the two *original* occurrences do
both predate, which selects BOTH-PREDATE — but that branch's own
pre-registered prediction is "the 31 Oct read predicts ZERO
ConnectTimeouts", and that is **already observably false**. So the
BOTH-PREDATE branch self-falsifies two days early and lands on the same
verdict as EITHER-POSTDATES. No interpretation was needed to break a
tie.

This is the second pre-registered rule in two days with a soft edge
(yesterday: "dominant failure" vs "400 only"). The lesson generalises:
**a pre-registration over a set of observations must say whether that set
is closed.** This one silently assumed it was.

## The larger finding: ConnectTimeout is fleet-wide, not symbol-specific

Every `ConnectTimeout` in the table arrives in a **multi-symbol burst at
a single flush instant**. Ten distinct bursts, 124 rows, **28 distinct
symbols**:

| flush instant | side | symbols |
|---|---|---|
| 2026-10-27 17:04:55Z | PRE | 4 — ARB, BNB, DASH, ZRO |
| 2026-10-28 03:04:57Z | PRE | 1 — BCH |
| 2026-10-28 04:00:36Z | PRE | 3 — AAVE, ARB, DASH |
| 2026-10-28 16:00:42Z | PRE | 2 — CHIP, ZEC |
| 2026-10-29 00:00:43Z | PRE | 8 — 1000SHIB, ASTER, CHIP, ENA, HUMA, HYPE, VVV, XPL |
| 2026-10-29 00:05:43Z | PRE | 1 — EVAA |
| 2026-10-29 03:00:49Z | PRE | 4 — APT, HBAR, ICP, NIL |
| 2026-10-29 04:00:36Z | PRE | 4 — APT, BANK, ERA, INJ |
| 2026-10-29 05:00:36Z | PRE | 3 — BTC, ETH, STRK |
| **2026-10-29 18:03:44Z** | **POST** | 4 — ARB, CRV, INIT, ZRO |

**BTCUSDT and ETHUSDT are in that list.** The two most liquid,
most-certainly-spot-listed pairs on the exchange take `ConnectTimeout`s
too. Whatever this is, it has nothing to do with symbol identity,
routing, or listing status.

### Two retractions of my own earlier framing

The 2026-10-29 status-code read called ARB's all-six-timeframe
`ConnectTimeout` a *mechanism* for ARB specifically. Both halves of that
are wrong:

- **ARB is not distinguished.** It appears in 3 of 10 bursts — the most
  of any symbol, but CHIP, DASH, APT and ZRO each appear in 2, and 23
  others in 1. A 3-vs-2 margin over 10 bursts is not a signature.
- **"All six timeframes" is not an ARB pattern.** It is the *common*
  pattern: 15 of 34 (symbol, burst) pairs show all six, and the
  remainder spread over 1, 2, 3 and 5. Six concurrent requests from one
  `asyncio.gather` failing together is what a momentary connect failure
  looks like for any symbol.

So ARB was never a special case needing a special explanation. It is an
ordinary member of a fleet-wide burst phenomenon, and the reason it
looked permanent across two cycles in the original symbol-level read is
that it happened to appear in two consecutive bursts.

### What the bursts point at

Nine of the ten cluster within ~5 minutes of an hour boundary
(`17:04`, `03:04`, `04:00`, `16:00`, `00:00`, `00:05`, `03:00`, `04:00`,
`05:00`, `18:03`). That is when every symbol's scoring cycle fires at
once, so it is the instant of peak concurrent outbound connections
**across the whole application** — flow_features, binance,
binance_futures_intermarket, bybit and mtf together.

This is why #617 could not have fixed it, and the reason is worth being
precise about: pooling removed socket churn **inside one module**. A
host-level resource ceiling (ephemeral ports, FDs, conntrack entries)
is reached by the *sum* of all modules' concurrent connections at the
boundary. One module reusing its sockets lowers the total but does not
move the ceiling, and does not stop the remaining modules from reaching
it together.

## Denominator — stated, not elided

Post-deploy exposure is **one carrying flush**, ~1h 55m, against a
pre-deploy window spanning 10-27 to 10-29. **No rate comparison is made
and none is available.** The pre-registered test did not need one: the
prediction was exactly zero, and a single occurrence falsifies it. That
is the whole reason the prediction was written as a count.

For the record, the one post-deploy flush carried:

| kind | rows | symbols |
|---|---|---|
| `HTTPStatusError_400` | 90 | 15 |
| `ConnectTimeout` | 21 | 4 |

(The 400s' `observed_count` accumulates across the whole daily bucket, so
it is not a post-deploy count and is not quoted as one. The
`ConnectTimeout` rows each carry `observed_count = 1` with
`first_seen_at = last_seen_at = 18:03:44Z`, so those *are* fresh
post-deploy rows.)

The 400s being untouched is **by design** — #617 deliberately left
endpoint selection alone. 15 symbols × 6 timeframes is defect (a), and
migrating it is the next item.

## Status of the pre-registered 31 Oct read

**Resolved early, and closed.** Its question — does pooling explain ARB's
`ConnectTimeout`s — is answered no, two days ahead of schedule, by an
occurrence the fix was supposed to have prevented. Re-running the same
query on 31 Oct would not add information about pooling.

The pooling hypothesis does **not** get downgraded to "helped but did not
fully fix it". The pre-registration ruled that reinterpretation out in
advance, deliberately: the mechanism claimed to remove the cause, not to
reduce its rate.

What would test the host-level hypothesis is a different measurement
entirely — connection-table and FD observation on the host at an hour
boundary, not a counter read. **No such test is pre-registered here, and
none is started.** Naming the candidates is as far as this goes without
authorisation.
