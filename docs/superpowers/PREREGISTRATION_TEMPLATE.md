# Pre-registration template

Copy this into `docs/superpowers/decisions/YYYY-MM-DD-<slug>.md` and
commit it **before** running the measurement. The git history of that
file is the evidence that nothing below was chosen after seeing a point
estimate.

`backend/tests/unit/test_preregistration_provenance.py` enforces the
σ-provenance block mechanically. Omitting it fails CI.

---

## Why there is a MANDATORY provenance field

Two existing documents already carried this rule as prose:

- `2026-09-17-brain-offline-eval-preregistration.md`: *"The raw
  per-trade sigma applies, never the paired one. Reusing 2.75% here
  would understate every bar by ~1.8x."*
- `2026-10-21-phase-a-preregistration.md`: *"For MFE_R and MAE_R the bar
  is computed from the realised per-cell sd, since those distributions
  have not been characterised."*

**Both refused a cross-population σ import. I made one anyway**, nine
days later, in `2026-10-26-mtf-backfill-preregistration.md`: σ = 5.12pp,
back-solved from a figure derived from **barriered** shadow trades
(TP/SL-clipped, floor visible in the data at exactly −5.00), and applied
to a target of **raw close-to-close forward returns**. The real σ was
**24.21pp** — 4.7× the assumption — and the arm SE came out at 7.50pp
against a 0.2–0.3pp decision bar, i.e. 25–37× the bar.

Prose did not stop it. A **required field** would have forced
"back-solved from a barriered-trade figure" to be written on the page,
where it looks wrong on sight. That is the whole argument: this is the
socket-guard and closed-allowlist move applied to analysis instead of
code — the one place this project had never applied it.

---

## TEMPLATE — copy from here down

# \<Title\> — pre-registration

**Status: pre-registration. Written and committed BEFORE the measurement
is run.** The git history of this file is the evidence.

## The population

The exact rows, with every filter, and the as-of timestamp. A rolling
window drifts, so a count without a timestamp is stale by construction.

## The hypothesis and the decision it changes

What would be done differently under each outcome. If no outcome changes
an action, say so and stop — that is a finding about the question, not a
reason to measure it.

## The bar

**σ-provenance:** `<population σ was MEASURED on>` — `<the query, table and
column, or the figure it came from>`

> **This line is mandatory and is checked by CI.** It must name the
> population σ was measured on and where the number came from.
>
> **REJECTED, and the check fails on these:**
> - "operator working notes", "working notes", "from notes" — a constant
>   with no derivation is not a provenance.
> - "assumed", "back-solved", "inferred from", "imported from" — if σ
>   was not measured on the target population, the bar is not a bar.
> - a σ measured on a **different** population from the target. State
>   both names; if they differ, the import is the defect.
>
> **BARRIERED vs RAW is the specific trap that produced this rule.** A
> TP/SL-clipped trade outcome and a raw forward return are different
> quantities with different dispersion — 2.52pp vs 20.87pp on the same
> 30-day window, an 8.3× ratio. If the target is one and σ came from the
> other, the bar is wrong by that factor.
>
> If the target distribution has not been characterised, say so and
> compute the bar from the realised per-cell sd instead, as Phase A did.

- n per arm, and the arm split if it is not even
- SE of the comparison, with the arithmetic shown
- the decision bar, and SE ÷ bar
- the comparison count and any multiplicity correction

**If SE exceeds the bar, say so HERE, before running it.** "Expected
unresolvable" written in advance is a finding; discovered afterwards it
is an excuse.

## The procedure, fixed before running

Numbered steps, including any step-0 abort condition and what makes it
fire.

## What a null result means

State it now. A non-arrival at the bar is sometimes the **predicted**
outcome of the leading hypothesis, in which case "still not significant"
is the confirmation, not an inconclusive. If that is the case here, write
the sentence that stops a future session misreading it.

## What this does NOT license

The conclusions that would *not* follow from a positive result.
