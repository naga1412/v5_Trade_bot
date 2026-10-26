"""Instance 10 of the cannot-fail family, in its purest form.

``evaluate_all_gates`` renders a ``_CheckResult`` with ``evaluated=False``
as the verdict string ``"not_evaluated"``. The SHORT score bar's **pass**
branch returned ``evaluated=False``, and so did its **bar-absent** branch.
Two different facts, one identical output string:

    bar applied, magnitude >= threshold  ->  "not_evaluated"
    bar absent (threshold is None)       ->  "not_evaluated"

There is nothing an observer can do with that log to tell the two apart, so
the discipline's two sentences -- "what does it say when the thing holds"
and "what does it say when the thing does not apply" -- came out identical.
That is the definition of a check that cannot fail, and it was sitting in
the one artefact built to answer "did the gate fire".

MEASURED ON PROD, 2026-10-26, live 1h SHORT, 3 days post-flip:
``min_score`` verdicts were ``block=2607``, ``not_evaluated=266``,
``pass=0`` -- while **110 cards were sent**. All 266 carried a reason of
the form ``|effective_score|=X >= 0.360``; zero carried the unset reason.
A gate that fired 266 times recorded zero firings.

WHY IT WAS LOAD-BEARING RIGHT THEN, not merely untidy: the SHORT overshoot
(36.655/day against a 16/day projection) could not be attributed to the bar
versus the score distribution, because the bar's own firings were
unauditable. The fix is one argument; the tests are what make the two
verdicts distinguishable from here on.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.gates.entry_quality import evaluate_all_gates, open_position_gate


def _settings(**over: Any) -> SimpleNamespace:
    base = {
        "DISABLE_SHORT_SIGNALS": False,
        "REGIME_GATE_ENABLED": False,
        "ADX_GATE_ENABLED": False,
        "PATTERN_BOOST_ENABLED": False,
        "MIN_ENTRY_SCORE_LONG": 0.36,
        "MIN_ENTRY_SCORE_SHORT": 0.36,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _signal(entry_score: float, direction: str = "SHORT") -> SimpleNamespace:
    return SimpleNamespace(direction=direction, entry_score=entry_score)


def _verdict_for(gate: str, verdicts: list[Any]) -> Any:
    return next(v for v in verdicts if v.gate == gate)


def test_a_short_that_passes_the_bar_is_recorded_as_pass() -> None:
    """The fix. Exact string, because the exact string is the defect."""
    v = _verdict_for("min_score", evaluate_all_gates(_signal(-0.42), _settings()))
    assert v.verdict == "pass", (
        f"a SHORT at magnitude 0.42 over a 0.36 bar recorded {v.verdict!r}. "
        "If that is 'not_evaluated' the dispatch log cannot distinguish a "
        "gate that fired from a gate that was never applied, which is what "
        "made 266 prod firings read as zero."
    )


def test_bar_absent_is_the_ONLY_thing_recorded_as_not_evaluated() -> None:
    """The other half, and the half that makes the first half mean something.

    If both branches said "pass" the log would be equally useless in the
    other direction. The pair of assertions is what pins the two facts to
    two distinct strings; either one alone can be satisfied by a constant.
    """
    applied = _verdict_for(
        "min_score", evaluate_all_gates(_signal(-0.42), _settings()))
    absent = _verdict_for(
        "min_score",
        evaluate_all_gates(_signal(-0.42), _settings(MIN_ENTRY_SCORE_SHORT=None)),
    )
    assert absent.verdict == "not_evaluated", absent
    assert applied.verdict != absent.verdict, (
        f"applied={applied.verdict!r} absent={absent.verdict!r} -- identical "
        "strings for two different facts is exactly the defect. The log must "
        "distinguish 'checked and passed' from 'no bar existed'."
    )


def test_a_short_that_fails_the_bar_is_recorded_as_block() -> None:
    v = _verdict_for("min_score", evaluate_all_gates(_signal(-0.20), _settings()))
    assert v.verdict == "block", v
    assert "0.200" in v.reason and "0.360" in v.reason, (
        f"reason {v.reason!r} must carry both the magnitude and the bar, so a "
        "reader can check the arithmetic without rerunning the gate"
    )


def test_all_three_verdicts_are_mutually_distinct() -> None:
    """Three facts, three strings -- asserted as a set, not pairwise.

    A pairwise chain can pass while two of the three collapse; comparing the
    cardinality of the set cannot.
    """
    seen = {
        _verdict_for("min_score", evaluate_all_gates(s, st)).verdict
        for s, st in (
            (_signal(-0.42), _settings()),                                  # pass
            (_signal(-0.20), _settings()),                                  # block
            (_signal(-0.42), _settings(MIN_ENTRY_SCORE_SHORT=None)),        # absent
        )
    }
    assert seen == {"pass", "block", "not_evaluated"}, seen
    assert len(seen) == 3


def test_the_dispatch_decision_is_UNCHANGED_by_the_fix() -> None:
    """No behaviour change, asserted rather than asserted-in-a-comment.

    ``open_position_gate`` branches on ``blocked`` only; ``evaluated`` is
    read solely by ``evaluate_all_gates`` for the log. If flipping it had
    moved a dispatch outcome, this would be a live-money change wearing an
    observability label.
    """
    assert open_position_gate(_signal(-0.42), _settings()).allow is True
    assert open_position_gate(_signal(-0.20), _settings()).allow is False
    # Bar absent -> no bar to fail -> allowed, exactly as before the fix.
    assert open_position_gate(
        _signal(-0.42), _settings(MIN_ENTRY_SCORE_SHORT=None),
    ).allow is True
    assert open_position_gate(
        _signal(-0.20), _settings(MIN_ENTRY_SCORE_SHORT=None),
    ).allow is True


def test_long_side_pass_verdict_was_already_correct_and_still_is() -> None:
    """The reference the SHORT side should have matched all along."""
    v = _verdict_for(
        "min_score",
        evaluate_all_gates(_signal(0.42, direction="LONG"), _settings()),
    )
    assert v.verdict == "pass", v
