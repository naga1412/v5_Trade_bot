"""THE NETWORK ALLOWLIST IS CLOSED: ten files, fifty-one tests, exactly.

WHY A CLOSED ALLOWLIST RATHER THAN A CEILING. The egress guard's value is
entirely FORWARD-looking: it exists so that the NEXT test to reach the
internet fails immediately instead of waiting to be noticed. An OPEN
allowlist destroys exactly that value, because adding an entry becomes
the path of least resistance -- cheaper than stubbing a transport, and
invisible in review as one more line in a list nobody counts. The guard
then protects nothing at the only moment it was built for.

So the contents are pinned as literals and asserted in BOTH directions.
An eleventh file, or a fifty-second test, fails here and requires a
deliberate edit to this file -- which shows up in review as a changed
count, not as an appended line.

TWO INDEPENDENT MECHANISMS, on purpose:

  * THE COUNTS come from pytest's OWN collection (`-m allow_network
    --collect-only`), so they are FORM-AGNOSTIC: any way of applying the
    marker, including dynamic application from a hook, changes a count.
    This is the backstop that does not depend on enumerating marker
    forms correctly.
  * THE BLANKET DETECTOR is a static AST check that NAMES the construct,
    so a failure is actionable rather than a word to grep for. It is
    form-specific by nature, which is why it has a case per form.

Construction mirrors `test_bar_fetch_endpoint_guard.py`'s closed
allowlist, for the same reason and after the same history.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest


BACKEND = pathlib.Path(__file__).resolve().parents[2]

# --- the pinned allowlist -------------------------------------------------
#
# Each entry is a file that reaches the real internet, with the number of
# tests in it that do. The counts are per-FUNCTION marks, not a blanket
# module-level `pytestmark`: a blanket mark granted network to 99 tests
# when only 51 need it, and a grant broader than the evidence is the same
# defect as having no control at all.
#
# WHY THIS DEBT IS REAL AND NOT COSMETIC: CI runners reach Binance from
# shared GitHub IP ranges, so these tests depend on an external service's
# tolerance of OTHER TENANTS' traffic. The failure mode is not "our calls
# are too frequent" -- it is a 418/429 earned by somebody else.
#
# Partitioned 2026-10-27: 51 STRUCTURE, 0 VALUE. No test here asserts on a
# value derived from live market data, so there is no false-green risk
# from market movement. See
# docs/superpowers/decisions/2026-10-27-network-test-partition.md.
CLOSED_NETWORK_ALLOWLIST: dict[str, int] = {
    "tests/unit/test_predictor.py": 15,
    "tests/integration/test_full_l9_pipeline.py": 8,
    "tests/integration/test_api_predict.py": 7,
    "tests/unit/test_predictor_brain_obs_inputs.py": 7,
    "tests/unit/test_dispatcher_e2e.py": 5,
    "tests/integration/test_intermarket_traps_e2e.py": 2,
    "tests/integration/test_l2_pipeline_e2e.py": 2,
    "tests/unit/test_pr_plumbing_1_funding_threading.py": 2,
    "tests/unit/test_tools_backtest.py": 2,
    "tests/unit/test_trading_dispatcher.py": 1,
}

# The guard's OWN opt-in control test. Counted separately and on purpose:
# it proves the escape hatch works and makes no outbound call, so folding
# it into the debt list above would overstate the debt by one and make the
# 51 stop matching the measured 51.
GUARD_CONTROL_FILE = "tests/unit/test_socket_guard.py"
GUARD_CONTROL_COUNT = 1

EXPECTED_FILES = 10
EXPECTED_TESTS = 51

# The nested collection imports the whole tree (~24s locally). CI runs
# with --timeout=120 per test, and the fixture below is module-scoped so
# this is paid ONCE for the file rather than once per test. The explicit
# subprocess timeout is higher than CI's so a slow runner produces a
# clear message here rather than an opaque per-test timeout.
_COLLECT_TIMEOUT_S = 300

_ARGUMENT = (
    "\n\nTHE NETWORK ALLOWLIST IS CLOSED ON PURPOSE.\n\n"
    "ADDING AN ENTRY HERE IS A DECISION, NOT A FIX.\n\n"
    "The egress guard's value is entirely FORWARD-looking: it exists so "
    "the NEXT test that reaches the internet fails immediately rather "
    "than waiting to be noticed. An open allowlist reduces that value to "
    "nothing, because appending a line is cheaper than stubbing a "
    "transport and invisible in review.\n\n"
    "If a test genuinely must reach the network, change BOTH the mark on "
    "the test AND the literal in this file, in one commit, with the "
    "reason written beside the entry. The duplication is the point -- it "
    "makes the change a diff a reviewer cannot skim past, and it makes "
    "the COUNT change rather than the list grow.\n\n"
    "If you are here because you added a test to a file that is already "
    "listed: the count is per-test for a reason. A blanket module-level "
    "mark granted 99 tests network access when 51 needed it. A grant "
    "broader than the evidence is the same defect as no control at all."
)


def _blanket_grants(src: str) -> list[str]:
    """Every MODULE- or CLASS-level form granting allow_network broadly.

    Returns human-readable findings, empty when clean.

    FORMS COVERED, each with a case in the calibration test below:

      1. ``pytestmark = pytest.mark.allow_network``        module level
      2. ``pytestmark = [pytest.mark.allow_network, ...]`` module, list
      3. ``pytestmark = (pytest.mark.allow_network,)``     module, tuple
      4. ``pytestmark: list = [...]``                      annotated
      5. ``pytestmark = ...`` as a CLASS attribute         class level
      6. ``@pytest.mark.allow_network`` on a CLASS         class decorator

    FORMS DELIBERATELY OUT OF SCOPE, with the reason:

      * ``@pytest.mark.allow_network`` on a FUNCTION -- that IS the
        intended per-test form, not a blanket grant.
      * ``pytest.param(..., marks=pytest.mark.allow_network)`` -- grants
        ONE parametrisation, which is narrower than per-test.
      * dynamic application via ``request.applymarker`` or a
        ``pytest_collection_modifyitems`` hook -- not statically visible
        at all, by construction.

    THE OUT-OF-SCOPE FORMS ARE STILL CAUGHT, just not here. The per-file
    counts come from pytest's own collection, which honours every form
    including the dynamic ones, so a grant by any mechanism changes a
    count and trips ``test_the_per_file_counts_are_exact``. This detector
    exists to NAME the construct; the count is the backstop that does not
    depend on enumerating forms correctly.
    """
    tree = ast.parse(src)
    findings: list[str] = []

    def _is_pytestmark_assign(stmt: ast.stmt) -> bool:
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        return any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets
        )

    for node in tree.body:
        if _is_pytestmark_assign(node) and "allow_network" in ast.unparse(node):
            findings.append("module-level pytestmark at line %d" % node.lineno)
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                if "allow_network" in ast.unparse(dec):
                    findings.append(
                        "class decorator on %s at line %d"
                        % (node.name, dec.lineno)
                    )
            for stmt in node.body:
                if (_is_pytestmark_assign(stmt)
                        and "allow_network" in ast.unparse(stmt)):
                    findings.append(
                        "class-level pytestmark in %s at line %d"
                        % (node.name, stmt.lineno)
                    )
    return findings


def _collect_marked() -> dict[str, int]:
    """Ask pytest which tests carry the marker.

    Uses the SAME collection CI uses (testpaths=["tests"] from
    pyproject.toml) rather than a glob: a glob over tests/unit/ is what
    missed tests/shadow/ and let three real Binance calls into CI.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-m", "allow_network",
             "--collect-only", "-q", "--no-cov", "-p", "no:cacheprovider"],
            cwd=BACKEND, capture_output=True, text=True,
            timeout=_COLLECT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - slow runner
        pytest.fail(
            f"nested pytest collection exceeded {_COLLECT_TIMEOUT_S}s. It "
            "imports the whole tree, ~24s locally. If a runner is genuinely "
            "this slow, raise _COLLECT_TIMEOUT_S -- do NOT narrow the "
            "collection to a subdirectory, because a narrowed glob is "
            "exactly what let three real Binance calls into CI."
        )
    out = proc.stdout + proc.stderr
    counts: dict[str, int] = {}
    for line in out.splitlines():
        line = line.strip().replace("\\", "/")
        if not line.startswith("tests/") or ": " not in line:
            continue
        path, _, n = line.rpartition(": ")
        if n.isdigit():
            counts[path] = int(n)
    assert counts, (
        "collected no allow_network tests at all -- the collection in "
        "this guard is broken, so its verdict means nothing.\n\n"
        + out[-2000:]
    )
    return counts


@pytest.fixture(scope="module")
def marked() -> dict[str, int]:
    return _collect_marked()


# Every test that requests `marked` pays the nested collection on first
# use. CI's default is --timeout=120 and the collection is ~24s locally;
# a runner 2-3x slower would trip that BEFORE the subprocess's own 300s
# budget could fire, so the 300s message would never be seen and the
# failure would read as an opaque per-test timeout. 400 > 300 makes the
# subprocess budget the binding constraint, which is the one with the
# explanation attached. Lowering the subprocess budget instead would risk
# a FALSE KILL on a slow runner.
_TIMEOUT_MARK = pytest.mark.timeout(400)


@_TIMEOUT_MARK
def test_the_allowlist_is_exactly_the_declared_set(marked) -> None:
    """Both directions. "No unexpected file" alone would let the list rot:
    an entry whose tests stopped needing network would sit here forever
    and the next reader could not tell a live exemption from a stale one.
    """
    actual = {k: v for k, v in marked.items() if k != GUARD_CONTROL_FILE}
    unexpected = sorted(set(actual) - set(CLOSED_NETWORK_ALLOWLIST))
    stale = sorted(set(CLOSED_NETWORK_ALLOWLIST) - set(actual))

    assert not unexpected, (
        "A NEW FILE REACHES THE REAL INTERNET FROM TESTS:\n  "
        + "\n  ".join(unexpected) + _ARGUMENT
    )
    assert not stale, (
        "These files are on the allowlist but no longer have any "
        "network-marked test -- remove them, the list has rotted:\n  "
        + "\n  ".join(stale) + _ARGUMENT
    )


@_TIMEOUT_MARK
def test_the_per_file_counts_are_exact(marked) -> None:
    """A fifty-second test in an already-listed file must fail too.

    Hand-derived from the CI run that first surfaced them:
    15 + 8 + 7 + 7 + 5 + 2 + 2 + 2 + 2 + 1 = 51.

    This is also the FORM-AGNOSTIC half of the guard: the numbers come
    from pytest's own collection, so a grant applied by any mechanism --
    including a class decorator or a dynamic hook the static detector
    cannot see -- changes a count here.
    """
    drift = {
        path: (expected, marked.get(path))
        for path, expected in CLOSED_NETWORK_ALLOWLIST.items()
        if marked.get(path) != expected
    }
    assert not drift, (
        "NETWORK-MARKED TEST COUNT CHANGED (file: expected -> actual):\n  "
        + "\n  ".join(
            f"{p}: {e} -> {a}" for p, (e, a) in sorted(drift.items())
        )
        + _ARGUMENT
    )


@_TIMEOUT_MARK
def test_the_totals_are_what_review_signed_off(marked) -> None:
    """The two numbers a reviewer actually reads.

    Pinned separately from the per-file dict so a change that preserves
    the total while moving a test between files still fails.
    """
    assert len(CLOSED_NETWORK_ALLOWLIST) == EXPECTED_FILES, (
        f"{len(CLOSED_NETWORK_ALLOWLIST)} files listed, "
        f"{EXPECTED_FILES} signed off" + _ARGUMENT
    )
    assert sum(CLOSED_NETWORK_ALLOWLIST.values()) == EXPECTED_TESTS, (
        f"{sum(CLOSED_NETWORK_ALLOWLIST.values())} tests listed, "
        f"{EXPECTED_TESTS} signed off" + _ARGUMENT
    )
    total_marked = sum(
        v for k, v in marked.items() if k != GUARD_CONTROL_FILE
    )
    assert total_marked == EXPECTED_TESTS, (
        f"{total_marked} tests carry allow_network, {EXPECTED_TESTS} "
        "signed off" + _ARGUMENT
    )


@_TIMEOUT_MARK
def test_the_guard_control_is_counted_separately_and_is_still_one(
    marked,
) -> None:
    """The guard's own opt-in test must not drift into the debt total.

    If it grows, the 51 silently becomes 52 and stops matching the
    measured population -- so it is pinned on its own.
    """
    assert marked.get(GUARD_CONTROL_FILE) == GUARD_CONTROL_COUNT, (
        f"{GUARD_CONTROL_FILE} has {marked.get(GUARD_CONTROL_FILE)} "
        f"network-marked test(s), expected {GUARD_CONTROL_COUNT}. It is "
        "the guard's own control and makes no outbound call; it is "
        "counted apart from the debt deliberately."
    )


def test_no_blanket_grant_remains() -> None:
    """Per-test marks only, in the listed files.

    A module- or class-level grant would cover every test in its scope --
    99 instead of 51 when this was first attempted. The per-file counts
    would then be whatever the file happens to contain and would drift
    upward every time an unrelated test was added, with no review signal.
    """
    offenders: list[str] = []
    for rel in list(CLOSED_NETWORK_ALLOWLIST) + [GUARD_CONTROL_FILE]:
        src = (BACKEND / rel).read_text(encoding="utf-8")
        offenders.extend(f"{rel}: {f}" for f in _blanket_grants(src))
    assert not offenders, (
        "BLANKET allow_network grant found in:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse a per-test @pytest.mark.allow_network. A blanket mark "
        "grants its whole scope, which is broader than the evidence and "
        "makes the counts in this guard meaningless."
    )


def test_the_blanket_mark_check_is_calibrated() -> None:
    """A case PER FORM, positive and negative.

    Without the positive cases the AST walk could match nothing and the
    detector would be decoration; without the negative cases it would
    flag comments and unrelated marks. Both halves exist because the
    first version was a substring match that passed every test it had and
    still misfired on a COMMENT containing the word "pytestmark".

    A form pytest honours that this misses is a broad grant sitting
    outside the named construct -- hence one case per form rather than
    one case overall.
    """
    # --- MUST FLAG: module level, four assignment shapes ---
    assert _blanket_grants("pytestmark = pytest.mark.allow_network\n")
    assert _blanket_grants("pytestmark = [pytest.mark.allow_network]\n")
    assert _blanket_grants("pytestmark = (pytest.mark.allow_network,)\n")
    assert _blanket_grants("pytestmark: list = [pytest.mark.allow_network]\n")
    assert _blanket_grants(
        "pytestmark = [pytest.mark.asyncio, pytest.mark.allow_network]\n"
    ), "a grant hidden among other marks in a list must still flag"

    # --- MUST FLAG: class level, both shapes ---
    assert _blanket_grants(
        "@pytest.mark.allow_network\nclass TestX:\n"
        "    def test_a(self):\n        pass\n"
    ), "a class decorator grants every test in the class"
    assert _blanket_grants(
        "class TestX:\n    pytestmark = pytest.mark.allow_network\n"
        "    def test_a(self):\n        pass\n"
    ), "a class-level pytestmark grants every test in the class"

    # --- MUST NOT FLAG ---
    assert not _blanket_grants(
        "# pytestmark allow_network mentioned in a comment\n"
    )
    assert not _blanket_grants("'''allow_network in a docstring'''\n")
    assert not _blanket_grants(
        "@pytest.mark.allow_network\ndef test_x():\n    pass\n"
    ), "a FUNCTION decorator is the intended per-test form"
    assert not _blanket_grants("pytestmark = pytest.mark.asyncio\n")
    assert not _blanket_grants(
        "def f():\n    pytestmark = pytest.mark.allow_network\n"
    ), "an assignment inside a function has no collection effect"
    assert not _blanket_grants(
        "@pytest.mark.parametrize('x', [pytest.param(1, "
        "marks=pytest.mark.allow_network)])\ndef test_x(x):\n    pass\n"
    ), "pytest.param marks are NARROWER than per-test, not broader"


def test_the_detector_names_the_construct_it_found() -> None:
    """The finding must say WHICH form, not just "something".

    A detector reporting "blanket mark found" without naming the
    construct sends the reader to grep for a word that may appear in
    three places in the file. The line number and shape are what make the
    failure actionable.
    """
    out = _blanket_grants(
        "class TestX:\n    pytestmark = pytest.mark.allow_network\n"
        "    def test_a(self):\n        pass\n"
    )
    assert len(out) == 1, out
    assert "class-level pytestmark" in out[0], out
    assert "TestX" in out[0], out
    assert "line 2" in out[0], out
