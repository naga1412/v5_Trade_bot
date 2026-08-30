# deploy.yml paths-ignore verification (2026-08-30)

PR #530 added `paths-ignore` (`**.md`, `docs/**`, `.github/workflows/ops-debug.yml`)
to `deploy.yml`'s push trigger. Before this, every push to `main`/`dev`
deployed unconditionally — discovered when merging PR #529 (an
ops-debug.yml-only probe addition, believed inert) silently triggered
a real prod restart that landed on a 15m candle boundary.

Two-proof verification, per operator instruction (both proofs required,
not one):

1. **Non-ignored change still deploys.** PR #530 itself touches
   `.github/workflows/deploy.yml`, which is not in the ignore list.
   Merging it triggered deploy run `33302105170` (`main`, 46s,
   completed cleanly). Confirmed.
2. **Doc-only change deploys nothing.** This file, under `docs/**`,
   is the test case for that half. If merging this PR produces zero
   new rows in `gh run list --workflow deploy`, proof #1 holds.
