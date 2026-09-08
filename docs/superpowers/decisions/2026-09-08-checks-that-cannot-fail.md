# Checks that cannot fail for the reason they exist

**Status: standing rule.** Written 2026-09-08 after the fourth instance
in five days. It keeps being re-derived in conversation, which is
precisely the failure it describes.

## The rule

**Before trusting a check, ask what result it would produce if the thing
it guards were broken. If the answer is "the same result", it is not a
check.**

For a test specifically: **assert IDENTITY against the single source,
never equality against a restatement of it.** Restating a formula to
verify it only proves the restatement matches itself.

This is not the same as "write better tests". A check that cannot fail
is worse than no check, because it converts an absent guarantee into a
believed one — and the belief is what stops anyone looking again.

## The four instances

### 1. A revert trigger keyed on a constant field — killed a working fix

The Stage-1 revert condition watched `predictions.symbol_source` for
loss of cohort variation. On prod that column was `established_top20`
on 100% of rows, because the threading had not landed yet. The check
could only ever return zero.

**Outcome:** it fired against a working change and #539 was reverted.
The check would have returned an identical result whether the fix was
perfect or absent.

### 2. A LONG/SHORT split proposed to test a 1.9% flip effect

To verify the pattern_stats direction-flip effect post-deploy, the
proposed observation was the LONG/SHORT ratio of 15m opens (24/32 at
n=56).

**Outcome:** caught before use. Binomial noise on n=56 is roughly
±6.7 trades — an order of magnitude larger than the ~1 trade/day the
effect predicts. A stable ratio would have been reported as
confirmation, and an unstable one as a regression, with neither
conclusion supported. The check's output was independent of the thing
it measured.

### 3. A structural test that examined only part of what it asserted

`test_dispatcher_passes_cohort_fields_to_signal_candidate` sliced
dispatcher source from `candidate = SignalCandidate(` to the next
`"    )"`. That boundary lands INSIDE the nested
`_compute_sl_distance_pct(...)` call, so the slice covered a fraction of
the argument list.

**Outcome:** caught on first run, by luck — it failed for its own bug
rather than passing silently. Had the truncated span happened to
contain the searched strings, it would have passed on a dispatcher
missing the exact argument it existed to assert. Rewritten to walk the
AST.

### 4. A threshold test asserting equality against a re-derived formula

`min_bars_for_vol("1h")` was asserted equal to
`(MIN_DAILY_BARS_FOR_VOL + 1) * 24` — the same arithmetic that defines
`HISTORY_SEED_BARS_1H` elsewhere. The test was a THIRD independent
statement of one requirement.

**Outcome:** caught in review, pre-merge. It would have kept passing
while the two constants it existed to pin drifted apart — which is the
exact drift `HISTORY_SEED_BARS_1H` was consolidated to end, after that
requirement had already been declared three times and diverged twice.

## The worked example of the fix

```python
# BEFORE — a third statement of the requirement. Passes while the two
# constants it pins diverge.
assert min_bars_for_vol("1h") == (MIN_DAILY_BARS_FOR_VOL + 1) * 24

# AFTER — identity against the single source. Cannot pass if they part.
assert min_bars_for_vol("1h") is HISTORY_SEED_BARS_1H
assert HISTORY_SEED_BARS_1H == (MIN_DAILY_BARS_FOR_VOL + 1) * 24
```

The second line still checks the arithmetic, but exactly once, against
the constant that owns it. The distinction is not stylistic: the first
form's failure mode is silence.

## Applying it

Three questions that catch most instances:

1. **What does this return if the guarded thing is broken?** Same
   answer as when it is healthy → not a check. (Instances 1, 4.)
2. **Is the effect I am looking for larger than the noise in what I am
   measuring?** If not, the observation cannot distinguish the
   hypotheses. (Instance 2.)
3. **Does this examine everything it claims to?** String-slicing,
   truncation, and sampling all silently narrow scope. Parse structure
   rather than matching text. (Instance 3.)

A related sibling worth naming: **a test can be a duplicate source of
truth as easily as a constant can.** Consolidating constants while
leaving a test restating the old formula rebuilds the seam one layer up
and hides it behind a green run.

## Related

- `2026-07-30-breakeven-stop-mechanic-design.md` — the pre-registration
  whose bar was remembered rather than read (n>=100 attributed to a doc
  that never contained it).
- `backend/docs/KNOWN_ISSUES.md` FU-48 — "not wired" meaning "the call
  returns a default"; the same class, at the level of a dependency
  rather than a check.
