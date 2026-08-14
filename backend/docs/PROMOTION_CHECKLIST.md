# Dev → Main Promotion Checklist

Standing checklist for every cherry-pick / promotion PR from `dev` to
`main`. Created 2026-08-14 after discovering `SHADOW_COOLDOWN_HOURS`
had silently diverged (dev `0.5h` vs main `4.0h`) since PR #286
(2026-07-14) merged directly to main and was never back-merged — see
`KNOWN_ISSUES.md` FU-42. Nobody intended that drift; it just rode along
unnoticed for a month because nothing forced a diff review of settings
that neither branch's changelog called attention to.

## Before merging any dev→main promotion PR

1. **Diff shadow-behavior-affecting settings between the two branches.**
   At minimum: `git diff origin/main origin/dev -- backend/app/config.py`
   and scan for any field that changes shadow's gates, thresholds,
   cooldowns, sizing, or entry/exit logic. If anything differs beyond
   what the promotion PR itself is intentionally shipping:
   - Confirm whether the difference is intentional drift you already
     know about (link the tracking issue), or
   - Flag it explicitly to the operator before merging — do not let an
     unrelated, unexamined settings diff ride along silently.
2. **If the active measurement window requires settings stability**
   (e.g. the breakeven-variant shadow measurement), treat any shadow-
   side settings diff as a hold, not a rubber stamp — mid-flight changes
   to shadow's gates/thresholds break measurement continuity with
   everything already collected, even if the change itself looks
   reasonable in isolation.
3. **Confirm the promotion PR's own diff matches its stated scope.**
   A squash-merge cherry-pick should touch exactly the files the
   original dev PR touched — nothing extra picked up along the way.
4. Follow the existing soak-class discipline (doc-only/ops-debug = 0h,
   observability-only = 12h spanning the nightly audit_verifier_task,
   recording-only = 24-48h, behavior-changing = 4-6h, PR9-class = 7+
   days plus explicit operator sign-off) before opening the promotion
   PR at all — this checklist is a final settings-diff gate on top of
   that, not a replacement for it.
5. Any PR merged directly to main without passing through dev first
   must be back-merged to dev the same day, or it becomes exactly the
   kind of drift this checklist exists to catch (see FU-42's origin
   story — this is not hypothetical).

## Scope

This checklist applies to promotions of code/config changes. It does
not apply to doc-only or ops-debug workflow-only changes, which are
already 0h/no-soak by the standing soak-class rules.
