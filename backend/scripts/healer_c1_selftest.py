"""Healer C1 self-test canary — injects a synthetic dispatch exception and
confirms C1 detects it, registers it as a novel type, and alarms critical.

TIER 2b (defect sweep 2026-08-06): `healer_known_error_types` had zero rows
ever — C1's alarm path had never been proven to fire on a real failure.
This script is that proof, kept forever as an on-demand canary (same shape
as `healer_selftest.py`'s alert-path canary).

Path exercised:
  * this script -> app.healer.findings.record_dispatch_error (the exact
    call dispatcher's outermost try/except makes on a real failure)
  * -> app.healer.detectors.detect_dispatch_error_rate (C1 itself)
  * -> app.healer.findings.record_finding (persists the CRITICAL finding,
    same as the real 5-min healer tick — see app/healer/runner.py)
  * -> app.ops.alert_routing.alert_admin(level='critical') (a REAL
    Telegram alert, same as a genuine C1 firing would send)

A freshly-named exception type is generated on every run so the "novel
class" branch fires every time — a repeatable canary, not a one-shot that
only proves itself once.

DOES NOT modify any dispatch tables, users, or live_trades. The synthetic
finding is tagged [SELFTEST] in its summary so it's filterable from a real
alarm the same way healer_selftest.py's canary is.

Usage (via ops-debug `healer-c1-selftest` probe):
    docker compose exec -T backend python /app/scripts/healer_c1_selftest.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import get_session_factory  # noqa: E402
from app.healer.detectors import detect_dispatch_error_rate  # noqa: E402
from app.healer.findings import record_dispatch_error, record_finding  # noqa: E402
from app.ops.alert_routing import alert_admin  # noqa: E402


_SELFTEST_TAG: str = "[SELFTEST]"


def _make_synthetic_exception() -> Exception:
    """A uniquely-named exception type so C1 always classifies it as
    NEVER-BEFORE-SEEN, regardless of how many prior selftest runs there
    have been."""
    tag = uuid.uuid4().hex[:12]
    cls = type(f"HealerC1SelftestException_{tag}", (Exception,), {})
    return cls(f"{_SELFTEST_TAG} synthetic dispatch error for the C1 canary (run {tag})")


async def _run() -> int:
    session_factory = get_session_factory()
    exc = _make_synthetic_exception()
    exc_type_name = type(exc).__name__
    print(f"healer_c1_selftest: injecting synthetic exception type={exc_type_name}")

    await record_dispatch_error(
        session_factory, exception=exc,
        context={"selftest": True, "tag": _SELFTEST_TAG},
    )
    print("healer_c1_selftest: recorded via record_dispatch_error (dispatcher_exception finding)")

    findings = await detect_dispatch_error_rate(session_factory)
    matching = [f for f in findings if f.details.get("exception_type") == exc_type_name]
    if not matching:
        print(
            f"healer_c1_selftest: FAIL — C1 did not report {exc_type_name} "
            f"as a finding (got {len(findings)} unrelated finding(s))",
            file=sys.stderr,
        )
        return 1

    finding = matching[0]
    print(
        f"healer_c1_selftest: C1 reported severity={finding.severity} "
        f"reason={finding.details.get('reason')} summary={finding.summary}",
    )
    if finding.severity != "critical" or finding.details.get("reason") != "novel_class":
        print(
            "healer_c1_selftest: FAIL — expected severity=critical "
            f"reason=novel_class, got severity={finding.severity} "
            f"reason={finding.details.get('reason')}",
            file=sys.stderr,
        )
        return 1

    # Persist + alert exactly as the real 5-min healer tick would
    # (app/healer/runner.py::run_one_tick) — this is a genuine finding
    # from C1's own logic, not a fabricated one.
    await record_finding(
        session_factory, detector_name=finding.detector_name,
        severity=finding.severity, summary=f"{_SELFTEST_TAG} {finding.summary}",
        details=finding.details,
    )
    routed_ok = False
    try:
        routed_ok = await alert_admin(
            f"[HEALER]{_SELFTEST_TAG} {finding.detector_name}: {finding.summary}",
            level="critical",
        )
    except Exception as e:  # noqa: BLE001 — the selftest must not raise up
        print(f"healer_c1_selftest: alert_admin raised: {e}", file=sys.stderr)
    print(f"healer_c1_selftest: alert routed_ok={routed_ok}")

    if routed_ok:
        print(
            "healer_c1_selftest: PASS — C1 detected the synthetic exception "
            "as novel and the critical alarm reached at least one channel"
        )
    return 0 if routed_ok else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
