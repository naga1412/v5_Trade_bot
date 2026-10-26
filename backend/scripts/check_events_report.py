#!/usr/bin/env python3
"""READ-ONLY report over `check_events` + the silence registry.

WHY THIS IS NOT A `sql-select` QUERY. The silence report cannot be
produced from SQL at all: :func:`app.ops.check_events.find_silent_checks`
iterates the Python registry ``EXPECTED_CHECKS`` and looks each name up,
precisely because a ``GROUP BY check_name`` over the table can only ever
name checks that already reported. The dangerous case -- a check that
never wrote a row -- has no row to group. So the registry has to be in
scope, which means in-container Python.

A FILE, NOT A BASE64 BLOB. `phase4-universe-discrepancy-check` assigned
``PY_SCRIPT_B64`` twice and bash kept the copy WITHOUT the fast-exit
section, so part (e) never ran for months (#600). A script that lives in
the repo is diffable, reviewable and cannot be silently shadowed.

Exits 0 always: this is a report, and a non-zero exit here would make the
workflow run red for a finding rather than for a malfunction. The
findings are in the output.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

import sqlalchemy as sa  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.ops.check_events import EXPECTED_CHECKS, find_silent_checks  # noqa: E402


def _hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


async def main() -> int:
    factory = get_session_factory()
    async with factory() as session:
        _hr("A. TABLE PRESENT?")
        try:
            total = (await session.execute(
                sa.text("SELECT COUNT(*) FROM check_events")
            )).scalar_one()
        except Exception as e:  # noqa: BLE001
            print(f"check_events is NOT QUERYABLE: {e}")
            print("If this is a fresh deploy, alembic 0044 has not run yet.")
            return 0
        print(f"check_events rows: {total}")

        _hr("B. THE SILENCE REPORT (registry-driven -- the whole point)")
        print(f"registry entries: {len(EXPECTED_CHECKS)}")
        for name, exp in sorted(EXPECTED_CHECKS.items()):
            print(f"  - {name} ({exp.kind}), window {exp.max_silence}")
        silent = await find_silent_checks(session)
        if not silent:
            print()
            print("ALL REGISTERED CHECKS ARE REPORTING INSIDE THEIR WINDOW.")
        else:
            print()
            print(f"{len(silent)} SILENT:")
            for s in silent:
                if s.never_reported:
                    print(f"  !! {s.name} ({s.kind}): NEVER REPORTED. No "
                          f"evidence it has ever run. {s.why}")
                else:
                    print(f"  !  {s.name} ({s.kind}): last {s.last_seen_at} "
                          f"({s.age} ago, window {s.max_silence}). "
                          f"Ran before, has STOPPED. {s.why}")

        _hr("C. PER-CHECK SUMMARY (what HAS reported)")
        rows = (await session.execute(sa.text(
            "SELECT check_name, check_kind, granularity, status, "
            "       COUNT(*) AS n_rows, SUM(observed_count) AS total_count, "
            "       MIN(first_seen_at) AS first_seen, "
            "       MAX(last_seen_at) AS last_seen "
            "FROM check_events "
            "GROUP BY check_name, check_kind, granularity, status "
            "ORDER BY check_name, granularity, status"
        ))).all()
        if not rows:
            print("(no rows yet)")
        for r in rows:
            print(f"  {r.check_name} [{r.check_kind}/{r.granularity}/"
                  f"{r.status}] rows={r.n_rows} count={r.total_count} "
                  f"first={r.first_seen} last={r.last_seen}")

        _hr("D. TOP SUBJECTS, LAST 7 DAYS")
        subj = (await session.execute(sa.text(
            "SELECT check_name, subject, SUM(observed_count) AS total "
            "FROM check_events "
            "WHERE last_seen_at >= NOW() - INTERVAL '7 days' "
            "  AND subject <> '' "
            "GROUP BY check_name, subject "
            "ORDER BY total DESC LIMIT 40"
        ))).all()
        if not subj:
            print("(no per-subject rows in the last 7 days)")
        for r in subj:
            print(f"  {r.check_name:<32} {r.subject:<16} {r.total}")

        _hr("E. NOTHING HERE TOOK AN ACTION")
        print("This probe is read-only and app.ops.check_events has no")
        print("remediation entrypoint (enforced by a structural test).")
        print("Any fix is a human decision -- see #571 for why.")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("ENV", "production")
    raise SystemExit(asyncio.run(main()))
