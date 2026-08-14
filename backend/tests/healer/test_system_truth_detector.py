"""Healer C5 (system_truth_detector) — control-flow tests.

The detector's four sections issue Postgres-specific SQL (FILTER (WHERE
...), jsonb_array_elements, ->/->>) that has no SQLite equivalent, so
these tests don't exercise that SQL directly -- it was validated
against real prod data via the standalone ops-debug `system-truth`
probe this detector was ported from (same queries, same first real run
found 41 genuine findings). What's tested here is the NEW logic this
module adds on top: the once-per-24h cadence gate and the baseline-diff
that decides what pages at critical vs what's a rolled-up "known issue"
info note vs what seeds silently on the very first run.
"""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.healer.system_truth_detector as st_mod
from app.healer.system_truth_detector import (
    BASELINE_DETECTOR_NAME,
    FINDING_DETECTOR_NAME,
    detect_system_truth,
)


@pytest.fixture(autouse=True)
def _reset_last_run(monkeypatch):
    """Wipe the module-level cadence gate between tests."""
    monkeypatch.setattr(st_mod, "_LAST_RUN_AT", None)
    yield
    st_mod._LAST_RUN_AT = None


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
    return engine


async def _seed_baseline(
    session_factory, keys: dict[str, list[str]], *, ever_varying: list[str] | None = None,
) -> None:
    payload = {"keys": keys}
    if ever_varying is not None:
        payload["ever_varying"] = ever_varying
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO healer_findings (detector_name, severity, summary, details) "
            "VALUES (:d, 'info', 'seed', :p)"
        ), {"d": BASELINE_DETECTOR_NAME, "p": json.dumps(payload)})
        await session.commit()


def _stub_sections(
    monkeypatch, findings: dict[str, list[str]], *, mature_tables: set[str] = frozenset(),
) -> None:
    """Replace all 4 section-scan functions with stubs returning a fixed
    finding dict, split arbitrarily across sections 1/2 (3/4 return {})
    -- the split doesn't matter, detect_system_truth merges them.
    ``mature_tables`` feeds _section1's second return value (tables past
    the CONSTANT grace period) for tests of the ever-varying tracking."""
    async def fake_section1(session):  # noqa: ARG001
        return dict(findings), set(mature_tables)

    async def fake_section_empty(*a, **kw):  # noqa: ARG001
        return {}

    monkeypatch.setattr(st_mod, "_section1", fake_section1)
    monkeypatch.setattr(st_mod, "_section2", fake_section_empty)
    monkeypatch.setattr(st_mod, "_section3", fake_section_empty)
    monkeypatch.setattr(st_mod, "_section4", fake_section_empty)


@pytest.mark.asyncio
async def test_first_run_seeds_baseline_without_paging(monkeypatch) -> None:
    """No prior baseline row exists -- every current finding is new by
    definition, but a fresh baseline must not page on anything."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _stub_sections(monkeypatch, {"table.col": ["BROKEN"]})

    out = await detect_system_truth(factory)

    severities = [f.severity for f in out]
    assert "critical" not in severities
    detector_names = {f.detector_name for f in out}
    assert FINDING_DETECTOR_NAME in detector_names
    assert BASELINE_DETECTOR_NAME in detector_names
    baseline_finding = next(f for f in out if f.detector_name == BASELINE_DETECTOR_NAME)
    assert baseline_finding.details["keys"] == {"table.col": ["BROKEN"]}
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_key_pages_critical_persisting_key_does_not(monkeypatch) -> None:
    """A finding present yesterday and today is a known issue (info,
    rolled up). A finding present today but NOT yesterday is a
    regression and must page at critical."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_baseline(factory, {"known.issue": ["CONSTANT"]})
    _stub_sections(monkeypatch, {
        "known.issue": ["CONSTANT"],
        "brand.new": ["BROKEN"],
    })

    out = await detect_system_truth(factory)

    criticals = [f for f in out if f.severity == "critical"]
    assert len(criticals) == 1
    assert criticals[0].details["key"] == "brand.new"
    assert "brand.new" in criticals[0].summary

    infos = [f for f in out if f.severity == "info" and f.detector_name == FINDING_DETECTOR_NAME]
    persisting = next(f for f in infos if "persisting" in f.summary)
    assert persisting.details["keys"] == ["known.issue"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_pure_constant_key_with_no_prior_variance_warns_not_pages(
    monkeypatch,
) -> None:
    """2026-08-14 recalibration: a NEW finding that is CONSTANT-only, on a
    mature table, with no evidence it was ever observed varying, is a
    new-field-constant (by design or coincidence) -- not a regression.
    Files at warning (triage, no Telegram page), not critical."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_baseline(factory, {}, ever_varying=[])
    _stub_sections(
        monkeypatch, {"flow_feature_snapshots.source": ["CONSTANT"]},
        mature_tables={"flow_feature_snapshots"},
    )

    out = await detect_system_truth(factory)

    assert not [f for f in out if f.severity == "critical"]
    warnings = [f for f in out if f.severity == "warning"]
    assert len(warnings) == 1
    assert warnings[0].details["key"] == "flow_feature_snapshots.source"
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_pure_constant_key_with_prior_variance_still_pages_critical(
    monkeypatch,
) -> None:
    """A CONSTANT finding for a key with prior-variance evidence on record
    (it demonstrably varied before) IS a real regression -- still pages
    critical even though it's a "new" key in yesterday/today diff terms."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_baseline(factory, {}, ever_varying=["predictions.final_score"])
    _stub_sections(
        monkeypatch, {"predictions.final_score": ["CONSTANT"]},
        mature_tables={"predictions"},
    )

    out = await detect_system_truth(factory)

    criticals = [f for f in out if f.severity == "critical"]
    assert len(criticals) == 1
    assert criticals[0].details["key"] == "predictions.final_score"
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_key_with_mixed_flags_still_pages_critical_even_if_constant(
    monkeypatch,
) -> None:
    """CONSTANT alongside another flag (e.g. SUSPECT) is a stronger signal
    than constancy alone -- the new-field-constant downgrade only applies
    when CONSTANT is the ONLY flag present."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_baseline(factory, {}, ever_varying=[])
    _stub_sections(
        monkeypatch, {"table.col": ["CONSTANT", "SUSPECT(min<0)"]},
        mature_tables={"table"},
    )
    monkeypatch.setitem(
        st_mod.TABLES, "table", {"ts_col": "ts", "columns": [("col", "cat", None)]},
    )

    out = await detect_system_truth(factory)

    criticals = [f for f in out if f.severity == "critical"]
    assert len(criticals) == 1
    assert criticals[0].details["key"] == "table.col"
    await engine.dispose()


@pytest.mark.asyncio
async def test_ever_varying_accumulates_and_persists_in_baseline(monkeypatch) -> None:
    """A mature column observed NOT constant today is recorded into the
    baseline's ever_varying set, unioned with whatever was there before."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_baseline(factory, {}, ever_varying=["other.col"])
    # table.col currently has no flags at all (healthy/varying) but its
    # table is mature -- it should be added to ever_varying.
    _stub_sections(monkeypatch, {}, mature_tables={"table"})
    monkeypatch.setitem(
        st_mod.TABLES, "table", {"ts_col": "ts", "columns": [("col", "cat", None)]},
    )

    out = await detect_system_truth(factory)

    baseline = next(f for f in out if f.detector_name == BASELINE_DETECTOR_NAME)
    assert set(baseline.details["ever_varying"]) == {"other.col", "table.col"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolved_key_reported_not_paged(monkeypatch) -> None:
    """A finding present yesterday but absent today is resolved -- noted
    at info, never critical (nothing to page about — things got better)."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_baseline(factory, {"fixed.now": ["BROKEN"], "still.bad": ["CONSTANT"]})
    _stub_sections(monkeypatch, {"still.bad": ["CONSTANT"]})

    out = await detect_system_truth(factory)

    assert not [f for f in out if f.severity == "critical"]
    resolved = next(
        f for f in out
        if f.detector_name == FINDING_DETECTOR_NAME and "resolved" in f.summary
    )
    assert resolved.details["keys"] == ["fixed.now"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_clean_sweep_reported_when_nothing_changed(monkeypatch) -> None:
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_baseline(factory, {})
    _stub_sections(monkeypatch, {})

    out = await detect_system_truth(factory)

    assert not [f for f in out if f.severity == "critical"]
    clean = next(
        f for f in out
        if f.detector_name == FINDING_DETECTOR_NAME and "clean sweep" in f.summary
    )
    assert clean.severity == "info"
    await engine.dispose()


@pytest.mark.asyncio
async def test_cadence_gate_skips_second_call_within_interval(monkeypatch) -> None:
    """The second call within 24h must do zero work -- not even open a
    session -- proven by making the session_factory raise if touched."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _stub_sections(monkeypatch, {"table.col": ["BROKEN"]})

    first = await detect_system_truth(factory)
    assert first  # did real work the first time

    def _boom():
        raise AssertionError("session_factory must not be called on a gated tick")

    out = await detect_system_truth(_boom)
    assert out == []
    await engine.dispose()
