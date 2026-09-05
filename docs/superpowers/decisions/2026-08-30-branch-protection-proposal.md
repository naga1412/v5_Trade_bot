# Branch protection proposal — written only, not applied (2026-08-30)

## Finding

Checked both branch-protection mechanisms GitHub exposes for this repo
(`gh api repos/.../branches/{main,dev}/protection` and the newer
rulesets API `gh api repos/.../rules/branches/{main,dev}`): **neither
`main` nor `dev` has any protection configured at all.** No required
status checks, no required reviews, nothing enforced by GitHub.
CI-green-before-merge has been operator/session discipline this whole
time, not a platform guarantee, on a repo where every merge to either
branch auto-deploys to a service the operator depends on (prod for
`main`, staging for `dev`).

## Proposal

Require the `backend` and `frontend` CI jobs (from `ci.yml`) as
required status checks on both `main` and `dev`. Do **NOT** require
`deploy` (from `deploy.yml`).

## Why not require `deploy`

This is the specific trap surfaced by the paths-ignore work (PR #530):
after that fix, a docs-only or `ops-debug.yml`-only push correctly
skips the `deploy` workflow entirely — GitHub never even queues a run
for it. A branch-protection rule that requires a check which never
reports leaves the PR permanently stuck in "Expected — waiting for
status." Requiring `backend`/`frontend` (which run on every push
regardless of path) avoids this failure mode entirely while still
gating on the thing that actually matters for merge safety: does the
code work, not did it deploy.

## Scope

Written only, per instruction. Applying this means either:
- `gh api -X PUT repos/naga1412/v5_Trade_bot/branches/main/protection` (and `dev`)
  with `required_status_checks.contexts = ["backend", "frontend"]`, or
- the equivalent via the GitHub UI (Settings → Branches → branch
  protection rule).

Neither has been run. This proposal is not applied to either branch.
