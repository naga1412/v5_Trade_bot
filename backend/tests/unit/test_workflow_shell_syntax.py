"""Workflow `run:` blocks are validated by EXTRACTION, never by retyping.

WHY THIS EXISTS. On 2026-10-29 a workflow patch wrote a literal ``\\n``
where a line continuation belonged. YAML still parsed -- a literal
backslash-n inside a block scalar is perfectly valid YAML -- so nothing
caught it. I then ran ``bash -n`` to check the block, **on a hand-retyped
approximation of it** pasted into a shell command. The approximation
parsed. The file was never checked.

That is the same defect as validating a reimplementation instead of the
deployed object, which ``backend/scripts/deploy_readback.py`` had been
written to prevent one hour earlier. The mechanical lesson: **a validator
must take the artifact as its input, not a copy of it.** A copy is a second
artifact, and the one that ships is then unverified.

So every check here reads ``.github/workflows/*.yml``, pulls each ``run:``
block out of the **parsed YAML**, and inspects that. Nothing is retyped at
any step. The largest block it covers is ops-debug.yml's ~195 KB one, which
is the exact block the incident happened in.

TWO CHECKS, AND THE SECOND IS THE LOAD-BEARING ONE.

``bash -n`` catches genuine syntax errors and is kept for that. It does
**not** catch the defect above, and that was established by planting the
real thing rather than assumed:

    ssh -i key "$U@$H" \\n          # mangled: literal backslash + 'n'
      "cd /opt && echo hi"

Bash reads ``\\n`` as an escaped ``n``, i.e. the plain character ``n``. So
the first line is ``ssh -i key "$U@$H" n`` -- a valid command with one
junk argument -- and the second line is a separate, also-valid command.
``bash -n`` returns 0. The correct form, ``\\`` then newline, also returns
0. The two are **syntactically indistinguishable**, so a syntax checker
cannot tell them apart in principle.

The first version of this file calibrated ``bash -n`` against a synthetic
"bad" block with an unterminated quote, which it does catch -- and that
synthetic case is not the real case. Calibrating a harness on invented
data instead of the real defect is the same error one level up: I had
validated the checker against a reconstruction of the bug. Planting the
actual bug is what exposed it.

The real defect is **lexical**, so the second check is lexical: no line in
a run block may end in a literal backslash followed by ``n``, ``t`` or
``r``. Measured against this tree: 727 legitimate trailing-backslash
continuations, 0 false positives.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[2].parent
WORKFLOWS = REPO / ".github" / "workflows"

BASH = shutil.which("bash")
BACKSLASH = chr(92)

# A GitHub expression is not shell. Bash reads `${{` as a bad substitution,
# so a block containing one would fail `bash -n` and the check would cry
# wolf -- and a guard that cries wolf gets switched off. One placeholder per
# expression, which is what the runner effectively does before any shell
# sees it.
GH_EXPR = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
PLACEHOLDER = "GH_EXPR"

NON_POSIX_SHELLS = {"python", "pwsh", "powershell", "cmd"}

# THE REAL DEFECT'S SIGNATURE. A line ending in backslash + n/t/r is a
# continuation whose newline got written as an escape sequence instead. A
# legitimate line continuation ends in the backslash itself, and a
# legitimate `printf "...\n"` ends in the closing quote, so neither
# matches. Verified against 727 real continuations in this tree with zero
# false positives.
MANGLED_CONTINUATION = re.compile(re.escape(BACKSLASH) + r"[ntr]$")


def _workflow_files() -> list[Path]:
    if not WORKFLOWS.is_dir():
        return []
    return sorted(p for p in WORKFLOWS.iterdir()
                  if p.suffix in {".yml", ".yaml"})


def _run_blocks(path: Path) -> list[tuple[str, str]]:
    """Every (step label, run body) in one workflow, from the PARSED yaml.

    Parsed rather than grepped: a ``run:`` block is a YAML block scalar
    whose indentation and continuation rules a regex would have to
    reimplement, and reimplementing the thing under test is the error this
    file is about.
    """
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return []
    out: list[tuple[str, str]] = []
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        # A job-level `defaults.run.shell` applies to every step in it.
        job_shell = ((job.get("defaults") or {}).get("run") or {}).get("shell")
        for i, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict) or "run" not in step:
                continue
            shell = step.get("shell") or job_shell or "bash"
            if str(shell).split()[0].lower() in NON_POSIX_SHELLS:
                continue
            label = step.get("name") or f"step[{i}]"
            out.append((f"{path.name}::{job_name}::{label}", str(step["run"])))
    return out


def _bash_n(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    f = tmp_path / "block.sh"
    f.write_text(script, encoding="utf-8")
    return subprocess.run(  # noqa: S603 - argv list, no shell
        [str(BASH), "-n", str(f)], capture_output=True, text=True, shell=False,
    )


# ===========================================================================
# Non-vacuity first
# ===========================================================================

def test_there_are_workflows_and_blocks_to_check() -> None:
    """A check that silently found nothing is not a check.

    The mirror case from the cannot-fail rules: if the directory moved or
    the extractor broke, every assertion below would pass over an empty
    list and report green on something it never looked at.
    """
    files = _workflow_files()
    assert files, f"no workflow files under {WORKFLOWS}"
    blocks = [b for f in files for b in _run_blocks(f)]
    assert len(blocks) >= 15, (
        f"only {len(blocks)} run-blocks extracted from {len(files)} "
        "workflow files -- the extractor is probably broken, not the "
        "workflows"
    )
    # The incident block specifically. If ops-debug's giant block ever
    # stops being extracted, this file stops covering the thing it exists
    # for, and that must be a failure rather than a quieter green.
    biggest = max(len(body) for _, body in blocks)
    assert biggest > 50_000, (
        f"largest extracted block is {biggest} bytes; ops-debug.yml's "
        "block is the one the 2026-10-29 incident happened in and it is "
        "far larger than that, so it is no longer being extracted"
    )


# ===========================================================================
# Check 1 -- syntax. Kept, but it is NOT the one that catches the incident.
# ===========================================================================

@pytest.mark.skipif(BASH is None, reason="no bash on PATH")
def test_every_run_block_parses_as_shell(tmp_path: Path) -> None:
    failures: list[str] = []
    for path in _workflow_files():
        for label, body in _run_blocks(path):
            proc = _bash_n(GH_EXPR.sub(PLACEHOLDER, body), tmp_path)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                failures.append(f"{label}:\n      {err}")

    assert not failures, (
        "a workflow `run:` block is not valid shell:\n    "
        + "\n    ".join(failures)
        + "\n\nThe block was EXTRACTED from the parsed YAML. Do not verify "
        "such a block by pasting an approximation of it into a shell -- a "
        "copy is a second artifact, and the one that ships is then "
        "unverified."
    )


@pytest.mark.skipif(BASH is None, reason="no bash on PATH")
def test_bash_n_CANNOT_catch_a_mangled_continuation(tmp_path: Path) -> None:
    """The honest calibration: prove the syntax check's blind spot.

    This is why `test_no_run_block_has_a_mangled_continuation` exists. If a
    future bash *did* reject the mangled form, this test fails and the
    lexical check could be reconsidered -- so the necessity is checked, not
    asserted once in a comment and left to rot.

    Hand-derived: `\\n` is an escaped `n`, which is the character `n`. So
    `ssh -i key "$U@$H" \\n` is the command `ssh -i key "$U@$H" n` with one
    junk argument, and the following line is a separate valid command. Two
    valid commands instead of one, with identical syntax to the correct
    single command.
    """
    mangled = (
        'ssh -i key "$U@$H" ' + BACKSLASH + "n\n"
        '  "cd /opt && echo hi"\n'
    )
    correct = (
        'ssh -i key "$U@$H" ' + BACKSLASH + "\n"
        '  "cd /opt && echo hi"\n'
    )
    assert _bash_n(mangled, tmp_path).returncode == 0, (
        "bash -n now rejects the mangled continuation. If that is real, "
        "the lexical check below may be redundant -- confirm before "
        "removing it."
    )
    assert _bash_n(correct, tmp_path).returncode == 0, (
        "bash -n rejects the CORRECT form, so this harness would cry wolf"
    )
    # The two forms are indistinguishable to the syntax checker, which is
    # the whole point: a syntax check cannot catch this defect even in
    # principle.
    assert MANGLED_CONTINUATION.search(mangled.splitlines()[0])
    assert not MANGLED_CONTINUATION.search(correct.splitlines()[0])


@pytest.mark.skipif(BASH is None, reason="no bash on PATH")
def test_bash_n_DOES_catch_a_real_syntax_error(tmp_path: Path) -> None:
    """The other direction, so check 1 is known to be load-bearing too.

    Without this, a `bash -n` invoked wrongly -- wrong flag, output read
    instead of exit code, file never written -- reads green on everything
    and check 1 becomes a check that cannot fail.
    """
    proc = _bash_n('echo "unterminated\n', tmp_path)
    assert proc.returncode != 0, (
        "bash -n accepted an unterminated quote, so check 1 cannot fail "
        "and its green means nothing"
    )


# ===========================================================================
# Check 2 -- lexical. THIS is the one that catches the incident.
# ===========================================================================

def test_no_run_block_has_a_mangled_continuation() -> None:
    offenders: list[str] = []
    for path in _workflow_files():
        for label, body in _run_blocks(path):
            for n, line in enumerate(body.splitlines(), start=1):
                stripped = line.rstrip()
                if MANGLED_CONTINUATION.search(stripped):
                    offenders.append(
                        f"{label} line {n}: ...{stripped[-60:]!r}"
                    )

    assert not offenders, (
        "a run-block line ends in a literal backslash-escape where a LINE "
        "CONTINUATION belonged:\n    "
        + "\n    ".join(offenders)
        + "\n\nA continuation ends in the backslash itself, then a real "
        "newline. Written as `" + BACKSLASH + "n` it is an escaped 'n', "
        "i.e. the plain character 'n' -- so the command gains a junk "
        "argument and the next line becomes a SEPARATE command. Both are "
        "valid shell, which is why `bash -n` returns 0 on it and why this "
        "lexical check exists (see "
        "test_bash_n_CANNOT_catch_a_mangled_continuation).\n\n"
        "This shipped on 2026-10-29 and was invisible: the YAML was valid, "
        "and the syntax check I ran was on a hand-retyped copy."
    )


def test_the_lexical_matcher_is_calibrated() -> None:
    """Positive and negative controls on the signature itself.

    The negatives matter most. A matcher that flagged every trailing
    backslash would fire on all 727 real continuations in this tree, and a
    guard that fires 727 times is a guard somebody deletes.
    """
    bad = [
        'ssh host ' + BACKSLASH + 'n',
        '  docker compose exec -T backend ' + BACKSLASH + 't',
        'psql -c "select 1" ' + BACKSLASH + 'r',
    ]
    good = [
        'ssh host ' + BACKSLASH,                      # real continuation
        'printf "done' + BACKSLASH + 'n"',            # escape inside a string
        'echo hi',                                     # nothing special
        'grep -n "a' + BACKSLASH + 'n" file.txt',     # escape mid-line
        'awk \'{print $1}\' ' + BACKSLASH,            # real continuation
    ]
    for line in bad:
        assert MANGLED_CONTINUATION.search(line), line
    for line in good:
        assert not MANGLED_CONTINUATION.search(line), line
