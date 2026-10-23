# Shadow caps R, live does not — quantified by lane

**Status: standing caveat, quantified.** Written 2026-10-23.

**This is the home for a divergence that previously existed only in
session memory.** The operator referred to a "paused Remediation Phase D
divergence doc"; **there is no such file in this repo** — searched by
filename and by content, and the only `Phase D` hits are unrelated SP
plans from May. Rather than file a quantified result into an
approximately-right document, the divergence gets its own record here.

## The divergence

| | shadow | live |
|---|---|---|
| stop | `max(close − 1.5·atr, close × 0.95)` | `close − 1.5·atr` |
| source | `app/shadow/engine.py:190` | `app/core/predictor.py:92` |
| **5% cap** | **yes** (PR #286, incident-driven) | **no cap at all** |
| multiplier | `SL_ATR_MULT = 1.5` | `1.5` hardcoded |
| ATR | `_atr(bars, period=14)` | same function, same period |

**The multiplier and the ATR function are identical** — verified from
source, not inferred. `R_live = 1.5 × ATR` exactly, so `ATR = R_live /
1.5` is an exact reconstruction. **The cap is the only difference.**

Consequence: wherever the cap binds, **shadow measured a tighter stop
than the live path would use.** Since `R = entry − stop`, shadow's R is
smaller there, so `MFE_R` and `MAE_R` are *inflated* on those rows.

## The bind rate, BY LANE — never pooled

The cap bind is recoverable from stored columns with no ATR needed: when
the cap binds the stop is exactly `0.95 × entry`, so
`(entry − stop)/entry = 0.05`; when ATR binds it is strictly below.

Closed LONG shadow trades, `1h` and `15m`:

| lane | n | share | mean R/entry | implied ATR/entry | **cap-bound** |
|---|---|---|---|---|---|
| **15m** | 3,549 | 75.3% | 0.01200 | 0.00800 | **2.03%** |
| **1h** | 1,164 | 24.7% | 0.02378 | 0.01585 | **10.48%** |
| *pooled* | *4,713* | — | *0.01491* | — | *4.12%* |

**The pooled 4.12% is the wrong number for any 1h-only question, and
quoting it was an error.** It is diluted by a 15m lane three times the
size, and 15m ATR is roughly half the 1h value (`0.02378 / 0.01200 =
1.98`, close to the √4 random-walk expectation). **Use 2.03% or 10.48%.
Never 4.12%.**

By `entry_score` decile (pooled lanes), the bind also concentrates at
the top: **7.01%** in deciles 9 and 10 against **2.55%** mid-range, and
`mean R/entry` rises monotonically **0.01336 (d5) → 0.01764 (d10)**.

## Why it is a footnote on 15m and a real caveat on 1h

Three reasons it does not disturb the recorded geometry results — and
the third is lane-dependent:

1. **Average R is nowhere near the cap.** Mean `R/entry` is 0.01491
   pooled against a 0.05 cap; ATR binds by a factor of ~3.4 on a typical
   trade. The cap is a tail mechanism.
2. **The bias direction is known and bounded.** On cap-bound rows
   shadow's R is smaller, so MFE_R and MAE_R are inflated — not
   arbitrary, and signed.
3. **The results that mattered are structurally insensitive.** The
   stop-width test ran on **DB-confirmed stop-outs** and reported
   **−0.9887R** against a −1R construction, a self-validation unaffected
   by a denominator shift on a subset. And the TP ladder's criterion was
   **range, not level** — see below.

**Reason 3 holds on both lanes. Reasons 1 and 2 weaken on 1h**, where
the bind is 1-in-10 rather than 1-in-50.

### Why the TP ladder survives — stated, not asserted

**The inflation is a property of the rows, not of the rung.** A
cap-bound trade has a smaller R at *every* take-profit level tested, so
its inflated contribution shifts **every rung in the same direction by
roughly the same amount.** The *levels* all move together; the
**range** — the criterion's actual statistic — barely moves.

Choosing range over level therefore made the criterion **structurally
insensitive to a denominator error affecting a subset.** That is the
second vindication of that design; the first was that it survived the
iso-expectancy arithmetic. The measured span was 0.0703R against a
0.15R bar, far outside what a 10% subset with a ~2x inflated R could
produce in a *range*.

## The operative caveat

**Any 1h-only geometry result carries a 1-in-10 denominator divergence
from live, concentrated in the high-volatility tail.** Not a correction
to apply retroactively — a caveat to carry, and to state when a 1h
geometry figure is quoted to a decision.

## Scope limits on this measurement

- **LONG only.** The SHORT side mirrors the cap (`min(close + 1.5·atr,
  close × 1.05)`) but was not measured.
- **This is the bind rate in SHADOW, which is a LOWER bound on
  divergence.** It cannot see cases where live would differ for other
  reasons, and it is not a reconciliation of the two geometries.
- **Pre- vs post-expansion it does not drift the dangerous way:** 1h
  cap-bind is **11.84% pre** against **8.54% post**, and 15m **2.45%**
  against **1.34%**. Both *lower* post-expansion. That fact closed a
  named challenge to the breakeven reversal — see
  `2026-10-21-breakeven-reversal.md`.

## Related

- `2026-10-22-a0-escalation-result.md` — the card whose band must be
  denominated in the **capped** R, because its evidence was.
- `2026-07-29-study-1-flat-geometry-ladder.md` — the range-not-level
  criterion.
- `backend/docs/KNOWN_ISSUES.md` FU-53 — the other measurement-scope
  divergence found the same week.
