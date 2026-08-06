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


async def _seed_baseline(session_factory, keys: dict[str, list[str]]) -> None:
    async with session_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO healer_findings (detector_name, severity, summary, details) "
            "VALUES (:d, 'info', 'seed', :p)"
        ), {"d": BASELINE_DETECTOR_NAME, "p": json.dumps({"keys": keys})})
        await session.commit()


def _stub_sections(monkeypatch, findings: dict[str, list[str]]) -> None:
    """Replace all 4 section-scan functions with stubs returning a fixed
    finding dict, split arbitrarily across sections 1/2 (3/4 return {})
    -- the split doesn't matter, detect_system_truth merges them."""
    async def fake_section1(session):  # noqa: ARG001
        return dict(findings)

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
