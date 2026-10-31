"""The tradability audit: the classification, and that it has a PRODUCER.

Two halves, and the second is the one the work order was about.

The classification is a pure function, so it is tested exhaustively over
the status combinations rather than on a sample. Expected values are
hand-derived from the definition of ``untradable`` -- "no market will take
an order, either side" -- not read off a first run.

The producer half exists because ``EXPECTED_CHECKS`` carries a trap written
into its own comment: *"Registering a check that was never wired would make
this detector report it silent forever on its first run, and twenty
standing false alarms is how an alarm channel gets ignored."* So registering
``universe_tradability`` is only safe if something really writes its row,
and ``test_the_flush_cycle_ACTUALLY_writes_the_row`` is what makes that a
checked fact instead of an intention.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.ops.check_events import EXPECTED_CHECKS
from app.shadow.universe_tradability import (
    audit_tradability,
    describe_market_status,
)


# ===========================================================================
# The classification -- exhaustive over the status combinations
# ===========================================================================

def _audit(**kw: Any) -> Any:
    base = {
        "members": set(),
        "open_positions": set(),
        "tradable_futures": set(),
        "tradable_spot": set(),
    }
    base.update(kw)
    return audit_tradability(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("on_futures", "on_spot", "expect_untradable"),
    [
        # Hand-derived from "untradable = not on futures AND not on spot".
        # Only the all-absent corner is a finding; the three others are
        # tradable somewhere, which is the distinction the first version of
        # the script collapsed.
        (True, True, False),    # dual-listed
        (True, False, False),   # futures-only perp -- normal
        (False, True, False),   # spot-only -- NOT a finding
        (False, False, True),   # halted/settling everywhere -- the finding
    ],
)
def test_untradable_is_a_TWO_market_predicate(
    on_futures: bool, on_spot: bool, expect_untradable: bool,
) -> None:
    a = _audit(
        members={"XUSDT"},
        tradable_futures={"XUSDT"} if on_futures else set(),
        tradable_spot={"XUSDT"} if on_spot else set(),
    )
    assert bool(a.not_tradable) is expect_untradable, (
        f"futures={on_futures} spot={on_spot}"
    )


def test_the_four_populations_are_disjoint_by_construction() -> None:
    """One worked example, every field hand-derived.

    members = {A, B}, open = {B, C}, futures = {A}, spot = {C}

      A  on futures           -> tradable, not a finding
      B  on neither           -> untradable AND a member AND open
      C  on spot only         -> tradable; open; not a USDT perp

    so: not_tradable={B}, held_open={B}, open_not_member={} (B is a
    member, C is tradable), open_spot_only={C}.
    """
    a = _audit(
        members={"AUSDT", "BUSDT"},
        open_positions={"BUSDT", "CUSDT"},
        tradable_futures={"AUSDT"},
        tradable_spot={"CUSDT"},
    )
    assert a.not_tradable == ("BUSDT",)
    assert a.held_open == ("BUSDT",)
    assert a.open_not_member == ()
    assert a.open_spot_only == ("CUSDT",)
    assert a.has_finding is True


def test_open_not_member_is_a_finding_ON_ITS_OWN() -> None:
    """An untradable open position that is not yet a member still counts.

    The override's no-prior-entry branch would ADMIT it on the next
    refresh, so it is a pending admission rather than a current member.
    `has_finding` keyed only on `not_tradable` would miss it, which is why
    the property is not `bool(self.not_tradable)`.
    """
    a = _audit(
        members={"AUSDT"},
        open_positions={"ZUSDT"},
        tradable_futures={"AUSDT"},
        tradable_spot={"AUSDT"},
    )
    assert a.not_tradable == ()
    assert a.open_not_member == ("ZUSDT",)
    assert a.has_finding is True, (
        "an untradable open position that the override would admit is a "
        "finding even though it is not yet in the snapshot"
    )


def test_a_spot_only_open_position_is_NOT_a_finding() -> None:
    """The exact 2026-10-29 false positive, pinned.

    PEPEUSDT is spot-TRADING; its perp is 1000PEPEUSDT. The v1 predicate
    tested futures membership alone and called it untradable.
    """
    a = _audit(
        members={"1000PEPEUSDT"},
        open_positions={"PEPEUSDT"},
        tradable_futures={"1000PEPEUSDT"},
        tradable_spot={"PEPEUSDT"},
    )
    assert a.not_tradable == ()
    assert a.open_not_member == ()
    assert a.has_finding is False
    assert a.open_spot_only == ("PEPEUSDT",)


@pytest.mark.parametrize(
    ("members", "futures", "spot", "vacuous"),
    [
        # EACH DISJUNCT IS ISOLATED. An all-empty case cannot tell which
        # clause fired, and a mutant that deleted only the `members == 0`
        # clause SURVIVED the first version of this test for exactly that
        # reason -- the other two were still true. One empty input at a
        # time is what pins all three.
        (set(), {"AUSDT"}, {"AUSDT"}, True),       # no snapshot
        ({"AUSDT"}, set(), {"AUSDT"}, True),       # no futures listing
        ({"AUSDT"}, {"AUSDT"}, set(), True),       # no spot listing
        (set(), set(), set(), True),               # nothing at all
        ({"AUSDT"}, {"AUSDT"}, {"AUSDT"}, False),  # genuinely clean
    ],
)
def test_a_clean_result_over_EMPTY_inputs_is_flagged_vacuous(
    members: set[str], futures: set[str], spot: set[str], vacuous: bool,
) -> None:
    """Zero findings over an empty input is not a clean bill of health.

    Without this the probe's happiest-looking row -- no findings at all --
    is exactly what a broken input produces. The False row matters just as
    much: a flag that is always on is noise, and noise is how a check gets
    switched off.
    """
    a = _audit(members=members, tradable_futures=futures, tradable_spot=spot)
    assert a.has_finding is False, "none of these rows should be a finding"
    assert a.inputs_look_empty is vacuous, (
        f"members={len(members)} futures={len(futures)} spot={len(spot)}"
    )


def test_market_status_names_each_market_explicitly() -> None:
    """No verdict is left as an inference from absence."""
    s = describe_market_status(
        "XUSDT",
        tradable_futures=set(),
        tradable_spot={"XUSDT"},
        routable={"XUSDT"},
    )
    assert "futures=not-TRADING/absent" in s
    assert "spot=TRADING" in s
    assert "has-history" in s


# ===========================================================================
# THE PRODUCER -- the half the work order was about
# ===========================================================================

def test_the_check_is_REGISTERED() -> None:
    entry = EXPECTED_CHECKS.get("universe_tradability")
    assert entry is not None, (
        "the audit is not in EXPECTED_CHECKS, so its silence is not a "
        "finding and 'the trigger re-opens when the audit reports "
        "non-zero' is still unenforceable"
    )
    assert entry.kind == "probe"
    # 5-minute producer, so four missed cycles. Derived, not picked: the
    # sibling counter on the same cycle uses the same window for the same
    # reason.
    assert entry.max_silence == dt.timedelta(minutes=20)
    assert len(entry.why) > 80, "a registry entry must say why it matters"


@pytest.mark.asyncio
async def test_the_RECORDER_writes_a_violation_row(monkeypatch: Any) -> None:
    """The recorder turns a finding into a row. NOT the cycle wiring.

    Named precisely because the first version of this test was called
    `test_the_flush_cycle_ACTUALLY_writes_the_row` and did not touch the
    flush cycle at all -- it called the recorder directly. A test whose
    name claims more than it checks is worse than a missing test,
    because it answers the question nobody then re-asks. The cycle
    wiring is checked separately below.

    The session is a stub rather than a real DB because what is under
    test is the mapping from audit result to row fields;
    `tests/ops/test_check_events.py` runs the real migration for the SQL.
    """
    import app.ops.check_events as ce

    calls: list[dict[str, Any]] = []

    async def fake_record_daily(session: Any, name: str, **kw: Any) -> None:
        calls.append({"name": name, **kw})

    async def fake_audit(session: Any) -> Any:
        return _audit(
            members={"AUSDT", "BUSDT"},
            open_positions={"BUSDT"},
            tradable_futures={"AUSDT"},
            tradable_spot={"AUSDT"},
        )

    monkeypatch.setattr(ce, "record_daily", fake_record_daily)
    import app.shadow.universe_tradability as ut
    monkeypatch.setattr(ut, "audit_from_cached_listings", fake_audit)

    found = await ce.record_universe_tradability(object())

    assert found is True, "BUSDT is on neither market, so this is a finding"
    assert len(calls) == 1, calls
    row = calls[0]
    assert row["name"] == "universe_tradability"
    assert row["status"] == "violation"
    assert row["increment"] == 1
    assert row["detail"]["not_tradable"] == ["BUSDT"]
    assert row["detail"]["held_open"] == ["BUSDT"]
    assert row["detail"]["inputs_look_empty"] is False


@pytest.mark.asyncio
async def test_a_CLEAN_cycle_still_writes_a_row(monkeypatch: Any) -> None:
    """Silence is not success -- rule 2. The ok row is the whole point.

    Without it, a dead producer and a healthy one look identical, which
    is the state the audit shipped in on 2026-10-29.
    """
    import app.ops.check_events as ce
    import app.shadow.universe_tradability as ut

    calls: list[dict[str, Any]] = []

    async def fake_record_daily(session: Any, name: str, **kw: Any) -> None:
        calls.append({"name": name, **kw})

    async def clean(session: Any) -> Any:
        return _audit(
            members={"AUSDT"},
            tradable_futures={"AUSDT"},
            tradable_spot={"AUSDT"},
        )

    monkeypatch.setattr(ce, "record_daily", fake_record_daily)
    monkeypatch.setattr(ut, "audit_from_cached_listings", clean)

    found = await ce.record_universe_tradability(object())

    assert found is False
    assert len(calls) == 1, "a clean cycle must still leave a row"
    assert calls[0]["status"] == "ok"
    assert calls[0]["increment"] == 0


@pytest.mark.asyncio
async def test_an_UNLOADED_CACHE_is_an_error_row_not_a_pass(
    monkeypatch: Any,
) -> None:
    """A check that reports ok when it could not run is the core defect."""
    import app.ops.check_events as ce
    import app.shadow.universe_tradability as ut

    calls: list[dict[str, Any]] = []

    async def fake_record_daily(session: Any, name: str, **kw: Any) -> None:
        calls.append({"name": name, **kw})

    async def unloaded(session: Any) -> Any:
        return None

    monkeypatch.setattr(ce, "record_daily", fake_record_daily)
    monkeypatch.setattr(ut, "audit_from_cached_listings", unloaded)

    found = await ce.record_universe_tradability(object())

    assert found is False
    assert len(calls) == 1
    assert calls[0]["status"] == "error", (
        "an audit that could not run must not report ok"
    )
    assert "unloaded" in calls[0]["detail"]["error"]


@pytest.mark.asyncio
async def test_the_cached_reader_returns_None_on_a_cold_cache() -> None:
    """Both halves of the cache are required, and neither is inferred."""
    from app.shadow import cohort_cache
    from app.shadow.universe_tradability import audit_from_cached_listings

    cohort_cache._reset_for_tests()
    try:
        assert await audit_from_cached_listings(object()) is None  # type: ignore[arg-type]
    finally:
        cohort_cache._reset_for_tests()

@pytest.mark.asyncio
async def test_the_FLUSH_CYCLE_calls_the_recorder(monkeypatch: Any) -> None:
    """The wiring itself: a 5-minute cycle must reach the recorder.

    This is the test that makes registering the check safe. EXPECTED_CHECKS'
    own comment warns that a registered-but-unwired check reports silent
    forever on its first run, and twenty standing false alarms is how an
    alarm channel gets ignored. Everything else in this file would still
    pass if nothing ever called the recorder.

    ORDER IS ASSERTED TWICE, not just the call.

    Before find_silent_checks, or the first cycle after deploy logs a
    spurious 'has NEVER reported' for a check it is about to produce.

    And before flush_mtf_fetch_failures, which is the one that cost
    something: that function advances `_last_flushed` /
    `_last_flushed_detail` as it writes, BEFORE the commit. A fallible
    call placed after it can roll the rows back while the watermarks
    stay advanced, and those increments are never written again. I
    introduced that window by adding the recorder after the flush.
    """
    import app.workers.check_events_flush as cef

    order: list[str] = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def commit(self):
            order.append("commit")

    async def fake_flush(session):
        order.append("flush_mtf")
        return 0

    async def fake_tradability(session):
        order.append("record_universe_tradability")
        return False

    async def fake_silent(session):
        order.append("find_silent")
        return []

    async def fake_hb(session_factory, name, **kw):
        order.append("heartbeat:" + str(kw.get("status")))
        return None

    monkeypatch.setattr(cef, "flush_mtf_fetch_failures", fake_flush)
    monkeypatch.setattr(cef, "record_universe_tradability", fake_tradability)
    monkeypatch.setattr(cef, "find_silent_checks", fake_silent)
    monkeypatch.setattr(cef, "record_heartbeat", fake_hb)

    await cef.run_one_flush_cycle(session_factory=lambda: _Session())

    assert "record_universe_tradability" in order, (
        "the 5-minute cycle never calls the recorder, so the check is "
        "REGISTERED BUT UNWIRED and find_silent_checks will report it "
        f"silent forever. Cycle did: {order}"
    )
    assert order.index("record_universe_tradability") < order.index(
        "find_silent"
    ), (
        "the recorder must run BEFORE find_silent_checks, or the first "
        f"cycle after deploy reports its own check silent. Order: {order}"
    )
    assert order.index("record_universe_tradability") < order.index(
        "flush_mtf"
    ), (
        "the recorder must run BEFORE flush_mtf_fetch_failures. That "
        "function advances its in-process watermarks as it writes and "
        "BEFORE the commit, so a fallible call placed after it can roll "
        "the rows back while the watermarks stay advanced -- those "
        "increments are then never written again, silently "
        "under-reporting the counter for the life of the process. "
        f"Order: {order}"
    )
    assert order[-1] == "heartbeat:ok", order
