"""A pytest invocation may not appear in a shell pipeline. CI enforces it.

``tests/run_suite.py`` already exists for exactly this, and exists because
of exactly this -- its own docstring records two verdicts read from ``$?``
after a pipeline in a single session. It recurred anyway on 2026-10-29,
piping pytest into ``tail`` and then reading ``EXIT=$?``, which is tail's
status and therefore always 0.

So the helper is not the missing piece. **A helper a caller can bypass is a
convention**, and this project has now watched the same convention fail in
the bar-fetch endpoint choice (four times), the cohort-tag default, and
here. The load-bearing half is never the helper; it is the check that makes
bypassing it fail.

WHY A PIPELINE IS THE WHOLE BUG. In POSIX shells ``$?`` after ``a | b`` is
**b's** status, not a's. So::

    # pytest-pipe-ok: the single example in this file, quoted so the
    # check has the exact shape it rejects written down somewhere
    python -m pytest tests/ | tail -5 ; echo "rc=$?"

prints ``rc=0`` whenever ``tail`` succeeds, which is always. The printed
verdict is not merely unreliable -- it is *structurally incapable of ever
reporting failure*, which is the mirror of a check that cannot fail. It
reads green on a red tree.

The honest limit of this check, stated rather than discovered later: it
scans FILES. The 2026-10-29 recurrence was an ad-hoc interactive command
that was never committed, and no repo-level check can see one of those.
What this check does is keep the pattern out of anything durable -- scripts,
workflows, runbooks, docs -- so that nobody learns it by copying it from
this tree, and so the one place that demonstrates it says why it is wrong.

WAIVERS ARE PER LINE AND COUNTED. A file-level allowlist would be too
coarse (``run_suite.py`` must quote the bad pattern in its own docstring,
but that should not exempt the rest of the file), and a list that can grow
is a check that cannot fail. So a line is exempt only if it carries an
inline marker WITH A REASON, and the total number of waivers in the tree is
pinned to a hand-counted integer below. Adding one is then a visible edit to
this file, and the number can only go down on its own.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2].parent
NEWLINE = chr(10)


def pathlib_read(p: str) -> str:
    """Read a file as text. Named rather than inlined so the one
    self-referential test below reads clearly."""
    return Path(p).read_text(encoding="utf-8")

SCANNED_SUFFIXES = {".sh", ".bash", ".yml", ".yaml", ".md", ".py", ".txt"}
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", "venv", ".venv", "dist", "build", "htmlcov",
    "worktrees", ".claude",
}

# The waiver. A reason is mandatory and must be substantive -- "ok" or "see
# above" would turn the marker into a mute button.
WAIVER = re.compile(r"pytest-pipe-ok:\s*(?P<reason>.{15,})")

# HAND-COUNTED, and the only legitimate way to raise it is to edit this
# line. FOUR waivers, each on a line that must show the wrong pattern:
#   1  tests/run_suite.py                   -- the incident it was built for
#   2  this file's docstring                -- the shape it rejects
#   3  decisions/2026-09-08-checks-that-cannot-fail.md -- the same incident
#   4  plans/2026-05-04-SP-0.7-multi-user-plan.md      -- HISTORICAL, see note
#
# Entry 4 is the only one that is not prose forbidding the pattern: it is a
# real instruction in a May 2026 plan. It is waived rather than edited
# because a plan is a record of what was planned, and its waiver reason
# carries the warning not to copy it.
EXPECTED_WAIVERS = 4

# A pytest INVOCATION, not the word "pytest". The command must start a
# command: line start, or after a separator. Without that boundary,
# `grep pytest foo | head` matches and the check cries wolf -- and a guard
# that cries wolf is a guard people switch off, which is how the
# `_IS_PREREG` matcher nearly went.
_CMD_START = r"(?:^|[;&|(]|\$\()\s*"
_RUNNERS = r"(?:(?:python[0-9.]*|py|uv|poetry|pdm|hatch)\s+(?:-m\s+|run\s+))?"
PYTEST_CALL = re.compile(_CMD_START + _RUNNERS + r"(?:pytest|py\.test)\b")

# A real pipe, never `||`. `[^|]` on both sides rather than a lookaround
# pair, so `a || b` cannot match on either of its two characters.
REAL_PIPE = re.compile(r"(?<!\|)\|(?!\|)")


_COMMENTISH = __import__("re").compile(r"^\s*(?:#|//|--)")
_WAIVER_LOOKBACK = 6


def _waived_above(lines: list[str], n: int) -> bool:
    """True if the comment block directly above line *n* carries a waiver.

    Bounded at _WAIVER_LOOKBACK lines and stops at the first
    non-comment line, so a waiver excuses the statement it is attached
    to and nothing else. An unbounded search upward would let one marker
    silence a whole file, which is the file-level allowlist this was
    designed to avoid.
    """
    i = n - 2  # 0-based index of the line above
    steps = 0
    while i >= 0 and steps < _WAIVER_LOOKBACK:
        above = lines[i]
        if not _COMMENTISH.match(above):
            return False
        if WAIVER.search(above):
            return True
        i -= 1
        steps += 1
    return False


def _scanned_files() -> list[Path]:
    out: list[Path] = []
    for p in REPO.rglob("*"):
        if p.suffix.lower() not in SCANNED_SUFFIXES or not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def _offending_lines(text: str) -> list[tuple[int, str]]:
    """Lines invoking pytest with a real pipe AFTER the invocation."""
    hits: list[tuple[int, str]] = []
    lines = text.splitlines()
    for n, line in enumerate(lines, start=1):
        # SAME LINE, OR ANYWHERE IN THE COMMENT BLOCK DIRECTLY ABOVE.
        # This took three attempts, and the two failures are the reason
        # the mechanism has its own calibration tests below. Same-line
        # only was unusable: the offending lines are long shell commands
        # and a reason long enough to be substantive does not fit after
        # one. One-line-above then failed too, because a substantive
        # reason wraps onto a second comment line and pushes the marker
        # two lines up. Walking the CONTIGUOUS comment block is what a
        # multi-line reason actually looks like, and it is bounded, so a
        # marker cannot reach across unrelated code to excuse something
        # far below it.
        if WAIVER.search(line) or _waived_above(lines, n):
            continue
        m = PYTEST_CALL.search(line)
        if m is None:
            continue
        # The pipe must come after the invocation; a pipe earlier on the
        # line feeds something INTO pytest's argv construction, which does
        # not misreport pytest's own status.
        if REAL_PIPE.search(line, m.end()):
            hits.append((n, line.strip()))
    return hits


# ===========================================================================
# Calibrate the matcher BEFORE trusting it on the tree
# ===========================================================================

# The pipe character is CONSTRUCTED rather than written literally in the
# table below. A table of literal offending lines would make this file its
# own biggest offender -- four of the seven hits on the first run were
# these rows -- and waiving them would bury the real waivers in
# self-reference. The shape stays readable; only the character is built.
_P = chr(124)  # |

@pytest.mark.parametrize(
    ("line", "offends"),
    [
        # --- the real incidents, verbatim in shape ---
        (f"python -m pytest tests/unit/foo.py {_P} tail -12 ; EXIT=$?", True),
        (f'python -m pytest -q 2>&1 {_P} grep -E "passed"', True),
        (f"pytest tests/ {_P} head -5", True),
        (f"py.test tests/ {_P} wc -l", True),
        (f"  uv run pytest tests/ {_P} tail -1", True),
        (f"cd backend && python -m pytest -q {_P} tail -3", True),
        (f"$(python -m pytest -q {_P} tail -1)", True),
        # --- correct forms, which must NOT be flagged ---
        # Redirect then read $?: this DOES report pytest's status.
        ("python -m pytest tests/ > /tmp/o.txt 2>&1; RC=$?", False),
        ("python tests/run_suite.py tests/unit/foo.py", False),
        ("python -m pytest tests/ && echo ok", False),
        ("python -m pytest tests/ || echo failed", False),
        # The WORD pytest, not an invocation. These are the false
        # positives a boundary-free matcher would produce.
        (f"grep -rn pytest . {_P} head -5", False),
        (f'echo "see pytest docs" {_P} tee notes.txt', False),
        (f"ls {_P} grep pytest", False),
        ("# pytest output can be piped to tail, which is the bug", False),
    ],
)
def test_the_matcher_is_calibrated(line: str, offends: bool) -> None:
    """Positive AND negative controls, because one direction proves nothing.

    A matcher hardwired to match passes every positive case; one hardwired
    to never match passes every negative case. Both directions are asserted
    on real lines -- the positives are the shapes that actually occurred,
    and the negatives are the false positives a looser matcher produces.
    """
    assert bool(_offending_lines(line)) is offends, line


def test_a_waiver_needs_a_SUBSTANTIVE_reason() -> None:
    """The marker is not a mute button."""
    bad = f"python -m pytest x {_P} tail -1  # pytest-pipe-ok: ok"
    assert _offending_lines(bad), "a reasonless waiver must not exempt"
    good = (
        f"python -m pytest x {_P} tail -1  "
        "# pytest-pipe-ok: documents the wrong pattern in order to ban it"
    )
    assert not _offending_lines(good), "a reasoned waiver must exempt"


# ===========================================================================
# The scan
# ===========================================================================

def test_no_file_pipes_a_pytest_invocation() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in _offending_lines(text):
            offenders.append(f"{path.relative_to(REPO).as_posix()}:{n}: {line}")

    assert not offenders, (
        "A pytest invocation is piped, so the exit code read afterwards is "
        "the LAST command's and not pytest's:\n  "
        + "\n  ".join(offenders)
        + "\n\nIn a POSIX shell `$?` after `a | b` is b's status. A verdict "
        "read that way can never report failure -- it is green on a red "
        "tree, which is the mirror of a check that cannot fail.\n\n"
        "USE THE HELPER THAT ALREADY EXISTS:\n\n"
        "    python tests/run_suite.py tests/unit/test_foo.py -k name\n"
        "    python tests/run_suite.py --lint\n"
        "    python tests/run_suite.py --prepush PATHS\n\n"
        "It calls subprocess.run with a LIST argv and shell=False, so there "
        "is no shell to pipe in: the code it returns is pytest's by "
        "construction, and the output comes back as a string beside it "
        "(`Result.output`, `Result.tail(n)`) for anyone who wanted the "
        "tail.\n\n"
        "If you are writing PROSE that must show the wrong pattern in order "
        "to forbid it, append an inline waiver with a real reason:\n"
        "    # pytest-pipe-ok: <why this line must show the bad pattern>\n"
        "and raise EXPECTED_WAIVERS in this file, which a reviewer will see "
        "as a changed integer rather than one more line in a list."
    )


def _load_bearing_waivers(text: str) -> list[int]:
    """Line numbers of markers that actually SUPPRESS an offending line.

    A raw count of the marker string was the first version and it
    counted 7 where 4 were real: the three extras were in this file,
    where the marker is DEFINED and tested. A marker in a regex literal
    is not a waiver.

    So a waiver is defined by what it DOES: blank it out, and if the
    scan gains an offender, it was load-bearing. That definition also
    buys the staleness property for free -- a waiver whose offending
    line was later fixed or deleted stops counting, so the pinned total
    drops and CI tells you to delete the dead exemption. The bar-fetch
    guard needed a separate test for that; here it falls out of saying
    what a waiver is.
    """
    lines = text.splitlines()
    baseline = len(_offending_lines(text))
    out: list[int] = []
    for idx, line in enumerate(lines):
        if not WAIVER.search(line):
            continue
        without = list(lines)
        without[idx] = "# (waiver removed for this probe)"
        if len(_offending_lines(NEWLINE.join(without))) > baseline:
            out.append(idx + 1)
    return out


def test_every_waiver_is_LOAD_BEARING_and_the_count_is_pinned() -> None:
    """Hand-counted, so a quiet new exemption fails CI.

    Separate from the scan above so a widened set of waivers is reported
    as "the guard was weakened" rather than silently making the scan
    pass -- the same separation the bar-fetch endpoint guard uses, for
    the same reason.
    """
    found: list[str] = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not WAIVER.search(text):
            continue
        for n in _load_bearing_waivers(text):
            found.append(f"{path.relative_to(REPO).as_posix()}:{n}")

    assert len(found) == EXPECTED_WAIVERS, (
        f"load-bearing waiver count is {len(found)}, pinned at "
        f"{EXPECTED_WAIVERS}:\n  "
        + "\n  ".join(found)
        + "\n\nThe count is hand-written on purpose. An allowlist "
        "that can grow without a visible edit is a check that cannot "
        "fail: the next instance would not fail this guard, it would be "
        "appended to it. If the count went DOWN, a waiver has gone "
        "stale -- the line it excused was fixed or removed -- and the "
        "dead exemption should be deleted rather than kept."
    )


def test_the_load_bearing_definition_excludes_the_MARKERS_DEFINITION() -> None:
    """This file defines and tests the marker; those are not waivers.

    The negative control for `_load_bearing_waivers`. Without it the
    count test passes for the wrong reason the moment someone adds
    another mention of the marker to a docstring.
    """
    text = pathlib_read(__file__)
    all_markers = [
        n for n, line in enumerate(text.splitlines(), start=1)
        if WAIVER.search(line)
    ]
    load_bearing = _load_bearing_waivers(text)
    assert len(all_markers) > len(load_bearing), (
        "this file mentions the marker more often than it uses it, so "
        "the load-bearing filter must discard some: "
        f"markers={all_markers} load_bearing={load_bearing}"
    )
    assert len(load_bearing) == 1, (
        "exactly one line in this file is genuinely excused -- the "
        f"docstring example. Got {load_bearing}."
    )
