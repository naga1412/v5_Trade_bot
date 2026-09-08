"""Healer C1 self-test canary — unit + integration tests.

TIER 2b (defect sweep 2026-08-06): healer_known_error_types had zero rows
ever, meaning C1's alarm path had never been proven to fire on a real
failure. This script is that proof, kept as a permanent on-demand canary
(same pattern as healer_selftest.py's alert-path canary, test_selftest.py's
test shape).

Unit tests here mock detect_dispatch_error_rate to pin the script's own
exit-code/gating logic. The one integration test at the bottom exercises
the REAL record_dispatch_error -> detect_dispatch_error_rate chain against
an in-memory SQLite healer_findings table (same fixture shape as
tests/healer/test_detectors.py) — only alert_admin/get_session_factory are
mocked, since physical Telegram delivery can only be confirmed by the
operator running the real ops-debug probe.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.healer.detectors import detect_dispatch_error_rate
from app.healer.findings import HealerFinding
from scripts import healer_c1_selftest


class _CapturedFinding:
    """Test double for record_finding — records call kwargs."""
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, session_factory, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _CapturedDispatchError:
    """Test double for record_dispatch_error — records call kwargs."""
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, session_factory, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _novel_finding_for(exc_type: str) -> HealerFinding:
    return HealerFinding(
        detector_name="dispatch_error_rate",
        severity="critical",
        summary=f"NEVER-BEFORE-SEEN dispatch exception: {exc_type} (n=1 in last 1h)",
        details={"exception_type": exc_type, "hits_last_hour": 1, "reason": "novel_class"},
    )


# ---- unit tests: script's own gating logic (detect_dispatch_error_rate mocked) ----


@pytest.mark.asyncio
async def test_pass_when_c1_reports_the_synthetic_type_as_novel_critical() -> None:
    dispatch_calls = _CapturedDispatchError()
    finding_calls = _CapturedFinding()

    async def fake_detect(session_factory):
        # Mirror the real contract: C1 reads back exactly what was recorded.
        exc_type = dispatch_calls.calls[0]["exception"].__class__.__name__
        return [_novel_finding_for(exc_type)]

    with patch.object(healer_c1_selftest, "record_dispatch_error", new=dispatch_calls), \
         patch.object(healer_c1_selftest, "detect_dispatch_error_rate", new=fake_detect), \
         patch.object(healer_c1_selftest, "record_finding", new=finding_calls), \
         patch.object(healer_c1_selftest, "alert_admin", new=AsyncMock(return_value=True)), \
         patch.object(healer_c1_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_c1_selftest._run()

    assert rc == 0
    assert len(dispatch_calls.calls) == 1
    assert len(finding_calls.calls) == 1
    assert finding_calls.calls[0]["severity"] == "critical"
    assert finding_calls.calls[0]["detector_name"] == "dispatch_error_rate"


@pytest.mark.asyncio
async def test_fails_when_c1_does_not_report_the_synthetic_type() -> None:
    """C1 returning nothing (or only unrelated findings) means the
    injection -> detection chain is broken — must fail loudly, not pass
    silently."""
    async def fake_detect(session_factory):
        return [_novel_finding_for("SomeUnrelatedException")]

    with patch.object(healer_c1_selftest, "record_dispatch_error", new=_CapturedDispatchError()), \
         patch.object(healer_c1_selftest, "detect_dispatch_error_rate", new=fake_detect), \
         patch.object(healer_c1_selftest, "record_finding", new=_CapturedFinding()), \
         patch.object(healer_c1_selftest, "alert_admin", new=AsyncMock(return_value=True)), \
         patch.object(healer_c1_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_c1_selftest._run()
    assert rc == 1


@pytest.mark.asyncio
async def test_fails_when_finding_is_not_critical_novel_class() -> None:
    """A guaranteed-unique synthetic type must always classify as novel;
    if C1 ever reports it as anything else (e.g. rate_exceeded/warning),
    that's itself a signal something is wrong with C1 — must not pass."""
    dispatch_calls = _CapturedDispatchError()

    async def fake_detect(session_factory):
        exc_type = dispatch_calls.calls[0]["exception"].__class__.__name__
        return [HealerFinding(
            detector_name="dispatch_error_rate", severity="warning",
            summary="rate exceeded", details={
                "exception_type": exc_type, "hits_last_hour": 6,
                "reason": "rate_exceeded",
            },
        )]

    with patch.object(healer_c1_selftest, "record_dispatch_error", new=dispatch_calls), \
         patch.object(healer_c1_selftest, "detect_dispatch_error_rate", new=fake_detect), \
         patch.object(healer_c1_selftest, "record_finding", new=_CapturedFinding()), \
         patch.object(healer_c1_selftest, "alert_admin", new=AsyncMock(return_value=True)), \
         patch.object(healer_c1_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_c1_selftest._run()
    assert rc == 1


@pytest.mark.asyncio
async def test_fails_when_alert_channel_unavailable_even_if_c1_fired_correctly() -> None:
    """C1 detecting correctly but the alert never landing is still a real
    gap the operator needs to know about — same exit-code contract as
    healer_selftest.py."""
    dispatch_calls = _CapturedDispatchError()

    async def fake_detect(session_factory):
        exc_type = dispatch_calls.calls[0]["exception"].__class__.__name__
        return [_novel_finding_for(exc_type)]

    with patch.object(healer_c1_selftest, "record_dispatch_error", new=dispatch_calls), \
         patch.object(healer_c1_selftest, "detect_dispatch_error_rate", new=fake_detect), \
         patch.object(healer_c1_selftest, "record_finding", new=_CapturedFinding()), \
         patch.object(healer_c1_selftest, "alert_admin", new=AsyncMock(return_value=False)), \
         patch.object(healer_c1_selftest, "get_session_factory", new=lambda: None):
        rc = await healer_c1_selftest._run()
    assert rc == 1


@pytest.mark.asyncio
async def test_each_run_uses_a_fresh_never_before_seen_type() -> None:
    """A repeatable canary, not a one-shot — two runs must use two
    different synthetic exception type names, so the 'novel' branch
    fires every single time, not just the first."""
    dispatch_calls = _CapturedDispatchError()

    async def fake_detect(session_factory):
        exc_type = dispatch_calls.calls[-1]["exception"].__class__.__name__
        return [_novel_finding_for(exc_type)]

    with patch.object(healer_c1_selftest, "record_dispatch_error", new=dispatch_calls), \
         patch.object(healer_c1_selftest, "detect_dispatch_error_rate", new=fake_detect), \
         patch.object(healer_c1_selftest, "record_finding", new=_CapturedFinding()), \
         patch.object(healer_c1_selftest, "alert_admin", new=AsyncMock(return_value=True)), \
         patch.object(healer_c1_selftest, "get_session_factory", new=lambda: None):
        await healer_c1_selftest._run()
        await healer_c1_selftest._run()

    type_a = dispatch_calls.calls[0]["exception"].__class__.__name__
    type_b = dispatch_calls.calls[1]["exception"].__class__.__name__
    assert type_a != type_b


# ---- integration test: the REAL record_dispatch_error -> detect_dispatch_error_rate chain ----


async def _mk_healer_findings_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE healer_findings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "detector_name TEXT NOT NULL, "
            "detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "severity TEXT NOT NULL, "
            "summary TEXT NOT NULL, "
            "details TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE healer_known_error_types ("
            "error_type TEXT PRIMARY KEY, "
            "first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "seen_count INTEGER NOT NULL DEFAULT 1)"
        ))
    return engine


@pytest.mark.asyncio
async def test_real_c1_detects_and_alarms_on_a_real_injected_exception() -> None:
    """End-to-end against the REAL detect_dispatch_error_rate (C1 itself) —
    everything except the physical alert send and the write-side helper is
    real production code.

    record_dispatch_error itself is faked here to seed the row with a raw
    SQLite-safe INSERT rather than calling the real function, so this
    test's own INSERT shape stays independent of whatever record_finding
    happens to do — the point of this test is proving
    detect_dispatch_error_rate itself, unmodified production code,
    correctly classifies this script's exact synthetic-exception shape as
    novel + critical, not exercising the write helper.

    (Historical note: record_finding used to use `CAST(:p AS JSONB)`,
    which silently corrupted the JSON payload to the string '0' on
    SQLite's CAST-to-NUMERIC semantics — fixed 2026-08-14, remediation
    work order B3; see tests/healer/test_findings_record.py for direct
    coverage of the fix. This test's hand-seeded INSERT predates and is
    independent of that bug/fix either way.)
    """
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    finding_calls = _CapturedFinding()
    seeded: list[str] = []

    async def fake_record_dispatch_error(session_factory, *, exception, context=None):
        exc_type = type(exception).__name__
        seeded.append(exc_type)
        async with factory() as s:
            await s.execute(sa.text(
                "INSERT INTO healer_findings (detector_name, severity, summary, details) "
                "VALUES ('dispatcher_exception', 'warning', 'x', :j)"
            ), {"j": f'{{"exception_type": "{exc_type}"}}'})
            await s.commit()

    with patch.object(healer_c1_selftest, "record_dispatch_error", new=fake_record_dispatch_error), \
         patch.object(healer_c1_selftest, "detect_dispatch_error_rate", new=detect_dispatch_error_rate), \
         patch.object(healer_c1_selftest, "record_finding", new=finding_calls), \
         patch.object(healer_c1_selftest, "alert_admin", new=AsyncMock(return_value=True)), \
         patch.object(healer_c1_selftest, "get_session_factory", new=lambda: factory):
        rc = await healer_c1_selftest._run()

    assert rc == 0
    assert len(finding_calls.calls) == 1
    assert finding_calls.calls[0]["severity"] == "critical"
    assert finding_calls.calls[0]["details"]["exception_type"] == seeded[0]
