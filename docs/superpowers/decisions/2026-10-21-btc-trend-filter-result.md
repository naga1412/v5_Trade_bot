# BTC trend filter — RESULT: fails. The strategy search is complete.

**Pre-registration:** `2026-10-20-btc-trend-filter-preregistration.md`,
committed to main in #575 **before** the probe implementing it existed
(#576). Nothing below was chosen after seeing an estimate.

**Verdict: FAIL on both directions, by a wide margin, with the primary
LONG effect carrying the WRONG SIGN for the hypothesis.** The one
remaining hypothesis is not supported by the one variable that could
have supported it.

## Result, in the pre-registered order

Population: 8,010 closed trades from 2026-05-01, **all labelled** (0
unlabelled — every trade had 20 prior daily closes). BTC MA20 series:
981 points from 1,000 daily bars, spanning 2024-02-14..2026-10-21;
trend-day balance uptrend=506 / downtrend=475.

### PRIMARY

| | LONG | SHORT |
|---|---|---|
| cells (up / down) | 2560 / 2081 | 1763 / 1606 |
| sd (up / down) | 3.572% / 3.149% | 2.767% / 2.886% |
| SE_diff | 0.0987% | 0.0976% |
| **3σ BAR** | **0.2962%** | **0.2928%** |
| mean uptrend | +0.0169% | −0.0187% |
| mean downtrend | +0.0570% | −0.0201% |
| **difference** | **−0.0401%** | **+0.0015%** |
| **σ** | **−0.41** | **+0.01** |
| verdict | **FAIL** | **FAIL** |

LONG lands at **14% of its bar**; SHORT at **0.5%** of its bar and is
essentially exactly zero. **LONG's sign is negative** — it did
marginally *worse* in BTC uptrends, the opposite of what a
trend-follower-missing-its-filter predicts.

**The bar had real power, as pre-registered.** The predicted SE_diff
was ≈0.096% and the realised figures are 0.0987% / 0.0976% — the bar
came in at ≈0.29%, exactly as projected, against a monthly table that
implied a gap near 0.5pp. This test could have passed. It did not.

### REPLICATION — temporal halves

| half | direction | n (up/down) | difference | σ | same sign as primary | within 2 SE_diff |
|---|---|---|---|---|---|---|
| FIRST (05-11..08-24) | LONG | 1128 / 1497 | **+0.2357%** | +1.70 | **No** | No |
| SECOND (08-24..10-21) | LONG | 1432 / 584 | **−0.4025%** | −2.50 | Yes | No |
| FIRST | SHORT | 659 / 721 | +0.1323% | +0.76 | Yes | Yes |
| SECOND | SHORT | 1104 / 885 | −0.1018% | −0.91 | No | Yes |

**The halves disagree in sign with each other, in both directions.**
LONG goes +0.236% then −0.403%; SHORT goes +0.132% then −0.102%. That
is the signature of noise, not of an effect that a split merely dilutes.

Note the second-half LONG cell reads **−2.50σ** on its own. Taken in
isolation and in the wrong direction, it is exactly the kind of
compelling single-window number that the 3σ bar and the replication
requirement exist to reject. It is also a direct demonstration of why
the honest prior mattered: cut the same data a different way and the
sign flips.

## What this closes

**The strategy search is complete.** The pre-registration stated this
outcome in advance: *"If it fails, the strategy search is genuinely
complete."*

The "trend-follower with the trend filter missing" reading of the
monthly table was built on six post-hoc monthly data points with May
already a counterexample. Tested properly — on a variable knowable at
entry, with cells and a bar fixed before the estimate — it is not there.
The monthly pattern that motivated it was month-level covariance that
does not survive being made tradeable.

**Do not re-open this on monthly aggregates.** Monthly BTC drift is
only knowable once the month has ended; it was ruled inadmissible in
the pre-registration for that reason and the ruling stands. Any future
attempt needs a different variable, knowable at entry, and its own
pre-registration.

## Three defects were fixed to obtain this read

The probe failed three times before producing a number, and none of the
failures were in the analysis:

1. **#577** — a stray `n` argument: a literal backslash-`n` mid-line
   made ssh run `n` as the remote command. #577 itself claimed to fix
   this and did not; #578 did.
2. **#578** — found it by extracting the step's `run` script, stubbing
   `ssh` as a function that prints its argument vector, and asserting
   the vector. **Asserting that a workflow file parses is not the same
   as asserting the command it builds is the command you meant.**
3. **#579** — asyncpg will not coerce a date string for a timestamptz
   bind. Third instance of that class in this repo's verification chain.
