"""Structural guards on `.github/workflows/ops-debug.yml`.

The probe registry is a 1,500-line bash `case` statement that no test has
ever looked at, and it has already shipped two defects of a kind a
structural check catches for free:

  * a probe that assigns ``PY_SCRIPT_B64`` TWICE, so the second copy
    silently shadows the first -- in `phase4-universe-discrepancy-check`
    the shadowed copy was the only one implementing the part (e) the
    registry description promises, so part (e) had never run;
  * embedded base64 scripts whose heredoc escaping collapsed `\\n`
    (FU-51), which is why the prod probes were migrated to files.

These tests are deliberately about SHAPE, not behaviour. They need no
network, no DB and no ssh.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ops-debug.yml"
SCRIPTS_DIR = REPO_ROOT / "backend" / "scripts"

# The 19 prod probes migrated to file-backed scripts. Listed explicitly
# rather than derived, so that silently reverting one to an embedded
# script fails here instead of shrinking a derived set to match.
MIGRATED_PROBES = (
    "brain-nan-stablecoin-repro",
    "brain-nan-warmstart-repro",
    "brain-sharpe-diagnosis",
    "btc-trend-filter-test",
    "direction-calibration-retrospective",
    "external-endpoint-healthcheck",
    "gate-counterfactual",
    "gate-regime-window",
    "l9-news-diagnosis",
    "l9-null-corrected",
    "layer-value-isolation",
    "layer-value-isolation-followup",
    "liquidity-floor-depth-sensitivity",
    "movers-vs-control-flow-signals",
    "oi-delta-decisive-analysis",
    "own-trade-entry-timing",
    "part3-entry-timing-all-trades",
    "predictions-null-boundary",
    "system-truth",
)

_ASSIGN = re.compile(r"^\s*PY_SCRIPT_B64=")
_SCRIPT_REF = re.compile(r"/app/scripts/([A-Za-z0-9_]+\.py)")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dropdown_options(workflow_text: str) -> tuple[str, ...]:
    """The probe names offered by the `probe` workflow_dispatch input.

    Parsed from the `options:` list, which is the authoritative set --
    anything not in it cannot be dispatched at all.
    """
    opts: list[str] = []
    in_options = False
    for line in workflow_text.split("\n"):
        if re.match(r"^\s{8}options:\s*$", line):
            in_options = True
            continue
        if in_options:
            m = re.match(r"^\s{10}- ([a-z0-9][a-z0-9-]*)\b", line)
            if m:
                opts.append(m.group(1))
                continue
            # the options list ends at the next key at a shallower indent
            if line.strip() and not line.lstrip().startswith("-"):
                break
    assert len(opts) > 50, f"parsed only {len(opts)} probe options, parser is wrong"
    return tuple(opts)


@pytest.fixture(scope="module")
def case_blocks(workflow_text: str, dropdown_options: tuple[str, ...]) -> dict[str, list[str]]:
    """{probe_name: body_lines} for the dispatch `case` statement.

    A block runs from its own label to the NEXT probe label, rather than
    to the next `;;` -- several probes contain nested `case` statements
    (`staging-verify` branches on TARGET) whose inner `;;` would end the
    block early. Over-capturing trailing comments is harmless because
    every assertion below matches assignments and command text, not
    prose.

    Labels are only recognised when the name is a real dropdown option,
    so inner labels like `staging)` cannot be mistaken for probes.
    """
    known = set(dropdown_options)
    lines = workflow_text.split("\n")
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^\s+([a-z0-9][a-z0-9-]*)\)\s*$", line)
        if m and m.group(1) in known:
            starts.append((i, m.group(1)))
    blocks: dict[str, list[str]] = {}
    for n, (idx, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        blocks[name] = lines[idx + 1:end]
    return blocks


# --- the shadowing guard: the defect that had been live -----------------

def test_no_probe_assigns_py_script_b64_more_than_once(case_blocks) -> None:
    """A second assignment silently discards the first.

    This is how `phase4-universe-discrepancy-check` ran for two months
    without its part (e): the (e)-bearing script was assigned, then
    overwritten on the very next line by a stale 10,852-byte copy. bash
    reports nothing, the probe succeeds, and the missing section looks
    like it was never asked for.

    MUTATION RUN: duplicated the `PY_SCRIPT_B64=` line in that block ->
    red, naming the probe.
    """
    offenders = {
        name: sum(1 for ln in body if _ASSIGN.match(ln))
        for name, body in case_blocks.items()
    }
    doubled = {k: v for k, v in offenders.items() if v > 1}
    assert not doubled, (
        f"probe(s) assign PY_SCRIPT_B64 more than once, so all but the last "
        f"are dead: {doubled}"
    )


# --- the migration guard ------------------------------------------------

def test_migrated_probes_carry_no_embedded_script(case_blocks) -> None:
    """The 19 prod probes must run a committed file, not an embedded blob.

    MUTATION RUN: re-added `PY_SCRIPT_B64="x"` to the `system-truth`
    block -> red.
    """
    regressed = [
        name for name in MIGRATED_PROBES
        if any(_ASSIGN.match(ln) for ln in case_blocks[name])
    ]
    assert not regressed, f"migrated probes reverted to embedded scripts: {regressed}"


def test_migrated_probes_reference_a_file_backed_script(case_blocks) -> None:
    """MUTATION RUN: deleted the `/app/scripts/...` reference from the
    `gate-regime-window` block -> red."""
    missing = [
        name for name in MIGRATED_PROBES
        if not any(_SCRIPT_REF.search(ln) for ln in case_blocks[name])
    ]
    assert not missing, f"migrated probes reference no /app/scripts file: {missing}"


def test_every_referenced_script_is_actually_committed(case_blocks) -> None:
    """A probe pointing at a script nobody committed fails only in prod.

    That is the #406/W4 class: the dispatch path existed, the file had
    never been promoted, and the probe failed at run time against a
    container that did not have it.

    MUTATION RUN: pointed `system-truth` at
    `/app/scripts/probe_does_not_exist.py` -> red.
    """
    absent: list[str] = []
    for name, body in case_blocks.items():
        for line in body:
            for fname in _SCRIPT_REF.findall(line):
                if not (SCRIPTS_DIR / fname).is_file():
                    absent.append(f"{name} -> {fname}")
    assert not absent, f"probes reference scripts not present in backend/scripts: {absent}"


# --- the wiring guard ---------------------------------------------------

def test_every_dropdown_option_has_a_dispatch_block(dropdown_options, case_blocks) -> None:
    """A probe in the dropdown with no case block is selectable and inert.

    Same class as every "executes but goes nowhere" finding in this
    project, at the level of the operator-facing menu.

    MUTATION RUN: renamed the `health-summary)` label -> red, naming it.
    """
    unwired = [p for p in dropdown_options if p not in case_blocks]
    assert not unwired, f"dropdown options with no dispatch block: {unwired}"


def test_migrated_probe_list_matches_reality(case_blocks) -> None:
    """Guards the fixture itself: every name in MIGRATED_PROBES must be a
    real probe, or the two assertions above would pass vacuously by
    KeyError-free luck on a typo.

    MUTATION RUN: added "probe-that-never-existed" to MIGRATED_PROBES ->
    red. Without this test the typo'd name raises KeyError instead, which
    is also red but says nothing useful.
    """
    unknown = [p for p in MIGRATED_PROBES if p not in case_blocks]
    assert not unknown, f"MIGRATED_PROBES names no such probe: {unknown}"
