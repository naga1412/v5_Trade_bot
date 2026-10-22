# A0 full-range escalation — result, and the closure of the strategy search

**Status: closed.** Implements `2026-10-22-a0-escalation-amendment.md`
(#599, amending #589/#590), all merged to main before the probe existed.
Run 2026-10-22, ops-debug run 39818555769.

**THE FINDING, and it is a finding rather than an absence: the scorer
predicts MAGNITUDE at +8.62σ and DIRECTION not at all.**

## Scope limit, stated first because it bounds everything else

**60 of 279 symbols returned 400.** The bar-fetch helper uses Binance
**spot** klines and this fleet is futures-first, so futures-only symbols
have no spot series. Resolved: **37,682 of 44,525 rows (84.6%), 217
symbols.**

The dropped cohort is systematically **`futures_poll`**, so this is the
**spot-listed subset**, not the population the pre-registration
described. Filed as **FU-53** (third instance of the class) with the
structural fix scoped. Nothing below is wrong, but `futures_poll` is
unmeasured in it.

## Power, as printed before any estimate

```
contrast gain vs in-band : ~2.5x  (NOT the ~7x the naive SE claims)
detectable slope         : ~1.5 R per score unit clustered
```

Realised contrast **0.2816** units against in-band's 0.1368 — **2.06×**,
slightly under the stated 2.5×. Implied slope **+1.251 R/unit** against
~1.5 detectable, i.e. the effect sits just inside the resolvable range.

## Clusters and the design effect

**205 symbol clusters. Design effect at horizon 10 = 3.48×** — the naive
SE was too small by **1.87×**.

| MFE_R @10 | naive | clustered |
|---|---|---|
| σ | **+6.89** | **+3.69** |

Day-clustering reads **+4.14σ** against symbol's **+3.69σ**, so
**symbol is the binding dependence axis** and the pre-registered primary
was the right choice.

## Cells, with the invariants

| dec | n | score | syms | sd(mfe10) | atr/close |
|---|---|---|---|---|---|
| 0 | 3768 | 0.0587 | 203 | 1.56 | 0.02139 |
| 4 | 3769 | 0.1308 | 199 | 1.64 | 0.02261 |
| 9 | 3769 | 0.3403 | **156** | 2.72 | 0.02245 |

- **`atr/close` is flat** (0.0214–0.0235, non-monotone). The score is
  **not** an ATR-volatility proxy, so the wrong-sign mechanism flagged in
  the amendment does not operate through R's denominator. This matters
  for the conclusion: the magnitude effect is **realised path**
  volatility beyond what ATR already captures.
- **The top decile holds 156 symbols against ~200 elsewhere.** Treatment
  *is* correlated with cluster — the exact mechanism that makes
  symbol-clustering primary rather than precautionary.
- **`sd(mfe10)` is heavy-tailed and non-monotone** (1.56 … 4.13), which
  makes mean-based contrasts fragile here and is worth remembering before
  anyone re-reads these point estimates as precise.

## The bar

**3.729σ** corrected (14 comparisons). 3.0 uncorrected. 3.0–3.729 is
SUGGESTIVE, re-test, not announced.

## Estimates

| target @10 | diff | clustered SE | σ | verdict |
|---|---|---|---|---|
| **MFE_R** | +0.3522 | 0.0954 | **+3.69** | **SUGGESTIVE — not announced** |
| **MAE_R** | +0.2848 | 0.0330 | **+8.62** | **PASS, WRONG SIGN** |

MFE missed the corrected bar by **0.037σ**: it needed +0.3557 R and
produced +0.3522.

### The asymmetry check fails, and it is the deciding check

```
MFE gain  +0.3522 R
MAE gain  +0.2848 R
NET       +0.0674 R        asymmetry 1.24x
```

**A1a's asymmetry was 10.3× (MFE +0.3357 against MAE +0.0326).** That
asymmetry is what earned A1a its credibility before the collider
downgraded it. Here it is **1.24×**: higher score produces larger
excursions in **both** directions, almost symmetrically, netting
+0.0674 R on a 0.2816-unit contrast.

**MAE_R passes the corrected bar decisively in the wrong direction while
MFE_R does not pass at all.**

### It replicates across windows, which promotes it from suggestive

A0's **in-band** MAE_R read **+3.02σ wrong sign**, recorded as
SUGGESTIVE and explicitly not announced. At full range it is **+8.62σ**,
same sign, on a population 18× larger and a contrast 2× wider. Per the
cross-window confirmation rule
(`adx_gate_cross_window_confirmation_lesson`), that is **confirmed**.

### Descriptive horizons, and the control firing

Nested, so **one observation measured four ways, never four results.**

| horizon | clustered σ | naive σ | verdict |
|---|---|---|---|
| 5 | +4.63 | +8.03 | PASS |
| 10 | +3.69 | +6.89 | SUGGESTIVE |
| 20 | **+2.86** | **+4.79** | **FAIL — naive-only, pre-declared failure** |
| 40 | **+1.54** | **+3.39** | **FAIL — naive-only, pre-declared failure** |

### Replication

Strictly non-overlapping subset, n=6,033, **design effect 1.12×** — near
1, as expected once windows do not overlap, which is itself a check on
the clustering: the inflation disappears when the dependence does.

+0.2825 R at +2.74σ. **Same sign, 0.50 SE from the primary** — passes
the pre-registered consistency criterion, which required consistency and
explicitly not re-significance.

### Secondary populations

- **untraded-only**: +3.53σ, contrast **0.2700** — *smaller* than the
  primary's 0.2816, so #590's literal anti-join did mildly re-truncate
  the range, exactly as the amendment predicted when it ruled the
  anti-join out of the primary.
- **confidence-gated (≥0.50)**: +3.12σ. SUGGESTIVE.

## THE CLOSURE: the strategy search on this scorer is COMPLETE

**And the completion statement is not "nothing works". It is: the scorer
measures HOW MUCH, never WHICH WAY.**

That is what the old "4–7× lift identifying movers" always meant, read
correctly. **Movers, not winners.** The scorer finds symbols about to
move; it carries no information about the direction they move in.

Everything measured across this search is consistent with that single
sentence:

- **final P&L moves for no variable, ever** — Phase A's entire scan.
- **MFE_R is systematically more responsive than final P&L** — because
  excursion size tracks movement, and movement is what the score knows.
- **MAE_R rises with the score too, nearly as much** — the other half of
  the same fact, and the half that kills the directional reading.
- **The TP ladder is iso-expectancy to 0.002R** and the **stop-width
  test is monotonically worse** — with a fixed stop and target, lifting
  both barriers' hit rates nets zero.
- **The BTC trend filter failed** and **mtf≥4 downgraded to a collider
  artefact** — no directional conditioner survived.

**Do not re-open this on a new aggregate, a new window, or a new
variable of the same kind.** The question "does the scorer predict
direction" has been asked in-band, at full range, across two targets,
four horizons, two clustering axes and a non-overlapping replication.
The answer is no, and the reason is now mechanistic rather than
statistical.

**What is NOT closed:** whether the scorer's own **0.05–0.56 effective
ceiling** is the constraint. A variable that never exceeds 0.57 across
44,525 observations may lack the dynamic range to express direction at
all. That is a question about building a different scorer, not about
measuring this one harder.

## Related

- `2026-10-22-a0-escalation-amendment.md` — the pre-registration, and the
  worked example of its control rejecting two naive-only results.
- `2026-10-22-phase-a-result.md` — the scan this escalates from.
- `2026-10-22-measurement-window-rule.md` — why a fixed window was
  mandatory here.
- `backend/docs/KNOWN_ISSUES.md` FU-53 — the spot-klines default that
  cost 60 symbols.
