# Checks that cannot fail for the reason they exist

**Status: standing rule.** Written 2026-09-08 after the fourth instance
in five days -- and a FIFTH turned up while writing it, then a SIXTH
while verifying an unrelated deploy hours later (both below), which is
the strongest evidence in the document that this is a default failure
mode rather than a bad week. It keeps being re-derived in conversation, which is
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

## Mechanising it

The three questions at the bottom of this doc are sound but rely on
someone thinking to ask them, which is exactly what failed in instances
1 and 3. Two mechanical steps, split by check type. **Neither half
covers the other** — mutation cannot reach an operational check, and a
written failure signature would not have caught either test.

### For TESTS: break the thing once and watch it fail

Before committing a guard test, **make the defect it guards against real
and confirm the test goes red.** Targeted mutation, by hand, in the file
you already have open — not a suite-wide framework:

- delete the argument the test asserts is passed
- change the constant it claims to pin
- return the wrong value from the function under guard

Then revert the mutation. If the test passed while the thing was broken,
it is not a check. **One run.**

This is deterministic where review is not. It would have caught instance
3 outright rather than by luck — deleting `symbol_source=` from the
dispatcher call would have left the string-sliced test green, visibly —
and instance 4 before review, since changing `HISTORY_SEED_BARS_1H` to
503 leaves a re-derived-formula assertion passing.

Worked example, instance 4:

```
1. Edit: HISTORY_SEED_BARS_1H = 503
2. Run the test.
   - Still green  -> the assertion restates the formula. Not a check.
   - Red          -> it pins the real constant.
3. Revert.
```

### For OPERATIONAL CHECKS: state the failure signature in writing, first

Revert triggers, pass conditions, soak criteria and post-deploy
verifications are not tests and mutation cannot reach them. For these,
**write down what result the check produces UNDER THE FAILURE IT GUARDS
AGAINST, at the time the check is defined** — before it is ever run.

The discipline is stating the failure signature in advance, not
interpreting the result afterwards. Interpreting afterwards is how a
zero gets read as a pass.

Instance 1 would have been exposed by one sentence:

> "If futures_poll is broken, this query returns zero."

...because the next line must then be:

> "...and it also returns zero when futures_poll is fine, because prod
> never writes that value."

Two lines, and the check is revealed as uninformative before it can
revert a working change.

Instance 2 fails the same way, with a magnitude instead of a value:

> "If the flip effect is real, the LONG/SHORT ratio moves by ~1
> trade/day. Binomial noise on n=56 is +/-6.7 trades."

Written down, the mismatch is arithmetic rather than judgement.

Applies equally to a green result: state what the check shows when the
guarded thing is FINE. If the two statements are the same sentence,
there is nothing to observe.

## The six instances

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

### 5. A behavioural test that bypassed the guard it was testing

Found **while writing this document**, which is itself the point.

Replacing instance 4's retired test, the first attempt called
`_entry_timing_recon_tick(...)` directly with the flag monkeypatched
False, then True, and asserted the recorded state differed. But the flag
is checked at the CALL SITE in `_maybe_open_position` -- invoking the
tick directly bypasses the guard entirely and records state either way.

**Outcome:** caught only because it referenced a helper that did not
exist and died with a `NameError`. Had that helper been present it would
have shipped green and meaningless, asserting the flag controlled
behaviour while proving nothing of the kind. Instance 3's
luck-not-process failure, repeating exactly.

The test was removed rather than repaired: the guarantee it claimed is
carried by the structural gate test, and no assertion about a constant
can substitute for looking at what the code produced.

**Why this instance matters most.** Four instances in five days could be
a bad week. A fifth appearing spontaneously in the act of writing the
rule down -- authored by someone actively holding the pattern in mind --
is evidence it is the DEFAULT failure mode of writing checks, not a run
of bad luck. Vigilance demonstrably did not prevent it. That is the
argument for the mechanical steps above: the mutation step would have
caught this in one run, because deleting the guard would have left the
test green.

### 6. A verification grep that could not have seen its own subject

Verifying the entry-timing recon after enabling it, the check for "is
recon producing output" grepped the `logs` ops-debug probe for
`entry_timing|recon`. It returned 1 hit, which read as confirmation.

The hit was the string `reconnect`, inside the probe's OWN grep pattern
echoed in the workflow source. And the `logs` probe filters to
websocket/live_worker lines, so it could never have surfaced recon
output at all -- the check was searching a stream that structurally
could not contain what it was looking for.

**Outcome:** caught on inspection of the matched line, seconds before it
would have been recorded as "recon confirmed running". Minutes after
shipping the first version of this document.

## The false-positive face of the same omission

Instances 1-6 are all checks that could not detect a real problem --
**false negatives**. The same omission produces the opposite error just
as easily.

Same day, verifying the recon: shadow trade closes ran 7 -> 7 -> 4 -> 1
-> 0 over four hours and were flagged as a decline needing explanation.

They were not a decline. That reading came from comparing an HOURLY
count against an **8.3/hr six-hour average**. The hourly **median is 2**,
zero-close hours are **33 of 169 (19.5%)** across seven days, and **6 of
15 (40%)** at that hour specifically. Nothing had declined -- the
earlier hours were unusually busy, and the "decay" was a return to
normal.

Cost: two queries and a paragraph of alarm. Cheap here, but the same
error against a revert trigger reverts a working change, which is
exactly instance 1.

**So the rule extends by one clause.** State what the measurement looks
like:

1. when the thing is **fine**,
2. when it is **broken**, and
3. **what its ordinary variance is.**

Without (3), a normal fluctuation reads as a finding and a real finding
reads as noise. (1) and (2) alone tell you the check can discriminate;
(3) tells you the threshold between them. An average is not a variance,
and comparing a single observation to a mean over a longer window is the
most common way to lose (3) without noticing.

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
   measuring, and do I know that noise as a VARIANCE rather than an
   average?** If not, the observation cannot distinguish the
   hypotheses -- in either direction. (Instance 2; and the
   false-positive section above, where an hourly count was judged
   against a six-hour mean.)
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
