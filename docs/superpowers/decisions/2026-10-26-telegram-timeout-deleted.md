# telegram_timeout.py — DELETED, and the reason is not "unused"

**Date:** 2026-10-26
**Status:** deleted. Operator reversed the earlier "wire it" ruling and
pre-authorised deletion conditional on the answer below.
**Order:** follow-up item 3, 2026-10-26.

---

## The question that decided it

> ANSWER FIRST: what is the consequence of it never having run?

**Nil. There is nothing it could ever have acted on.**

| check | result |
|---|---|
| `expire_pending_approvals` callers in app code | **0** — absent from `worker_registry.py`, absent from `main.py`, no script, no cron, no workflow |
| writers of the `pending_approval_until=` marker | **0** anywhere in the repo, `tools/` included |
| prod `rl_checkpoints` rows carrying the marker | **0** of 90 |
| functional consumers of `APPROVAL_TIMEOUT_DAYS` / `REMINDER_INTERVAL_DAYS` | **this module only** |

So there were no stale approvals to count, and there never could be: the
consumer existed and the **producer was never built**. Its own docstring
names the producer —

> "The cron writes `notes='pending_approval_until=<ISO timestamp>'` when
> it registers the candidate"

— and `tools/ml/register_brain.py` writes `notes` only from a `--notes`
CLI argument. The spec'd write never landed.

## AND IT IS SUPERSEDED, WHICH IS THE STRONGER REASON

"Unused" alone would be a weak case for deletion. This is not unused —
it is **answering a question that no longer exists.**

The 7-day auto-reject was designed for a world where an unapproved brain
candidate would otherwise **auto-activate**. The timeout was a *race
against unwanted promotion*: approve within 7 days or the candidate is
rejected, so that nothing gets promoted by default.

**#571 and #613 inverted that default.** The bootstrap branch now
**refuses unless explicitly opted in** (`bootstrap_blocked`), in
`register_brain.py` and in `champion_challenger.evaluate_challenger`
respectively. Under the inverted default an unapproved candidate sits as
an inactive row **indefinitely, and that is the correct state** — not a
leak, not a pending obligation. The hold is permanent by design, so the
thing this module protected against cannot happen.

A timeout that forces a decision is only needed when *not deciding* is
dangerous. Refuse-by-default made not-deciding the safe state.

## Why it was NOT wired instead

The earlier ruling was to wire item 3(a)'s `(selected, advanced)` progress
emitter into it. That was reversed once it became clear the worker was
dead rather than live, and the reversal is right:

- Wiring a progress emitter into a **live** worker is observability.
- **Activating a dead worker on the approval path is enabling dark code.**

And this project is 3-for-3 that measuring first was correct: L2 dark was
a real loss, calibration dark was no loss at all, PR9's DCA was correctly
deleted. "Wire it to be safe" is not a decision. The footprint was
measured, came back zero, and the zero is what authorised the delete.

There is also a self-referential reason not to register it with the
watchdog: `check_events.EXPECTED_CHECKS` reports a check that never
emits as **SILENT forever**. Registering a worker that does not run would
have manufactured a standing false alarm, which that registry's own
docstring exists to prevent.

## What was deleted, and what deliberately was not

**Deleted:**
- `backend/app/ops/telegram_timeout.py`
- `backend/tests/unit/test_telegram_timeout.py`

**Kept, with a loud comment:** `APPROVAL_TIMEOUT_DAYS = 7` and
`REMINDER_INTERVAL_DAYS = 1` in `app/ops/telegram_bot.py`. They now have
**no functional consumer at all**, and a constant that looks like live
configuration but enforces nothing is precisely the dead-config trap this
project has hit repeatedly (`trap_enabled` never wired into scoring,
FU-28, FU-19). So they carry an explicit **NOT ENFORCED ANYWHERE** block
stating that the policy they describe is a record of intent and that a
timeout on brain approvals would have to be *built*, not re-enabled.

Removing them outright would mean editing the live **trade**-approval
module and its tests for a cosmetic cleanup, which is more blast radius
than the cleanup is worth. Naming the trap costs two lines and closes it.

**Also kept:** `backend/docs/KNOWN_ISSUES.md`'s mention of the file,
which is an incidental entry in a ~20-module list about per-call httpx
construction — not a claim about this feature.

## If a brain-approval timeout is ever wanted again

Build the **producer** first, and only then the consumer. The failure here
was not that the expiry logic was wrong — it was correct and had passing
tests for months. It was that a consumer shipped without the write that
feeds it, and nothing anywhere compared the two. That is the
`update_pattern_stats` shape: passing unit tests, wired to nothing,
invisible *because* the tests pass.
