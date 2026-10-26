"""Verify the verdict harness on KNOWN states before trusting it anywhere.

The standing rule this enforces on itself: a harness is verified against a
state whose answer is known *before* it is used to judge anything. The
session that produced ``tests/run_suite.py`` had already shipped one harness
that read RED on a clean tree (grepping pytest output under a
summary-suppressing config) and twice read a pipeline's exit code as
pytest's. All three were harness faults that a known-answer check would have
caught in one line.

So this file runs the runner against a test file that MUST pass and one that
MUST fail, and asserts both directions. One direction is not enough: a
runner hardwired to return 0 passes the first check, and a runner hardwired
to return 1 passes the second.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_suite import Result, pytest as run_pytest  # noqa: E402

PASSING = '''
def test_it_passes():
    assert 1 + 1 == 2
'''

FAILING = '''
def test_it_fails():
    assert 1 + 1 == 3, "deliberate failure -- this file exists to be red"
'''


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _run_isolated(path: Path) -> Result:
    """Run one out-of-tree file with rootdir pinned to its own directory.

    Without this pytest infers a rootdir by walking up from the temp path to
    the drive root and then tries to collect it, which on Windows dies on
    ``C:/Documents and Settings`` with a PermissionError -- a collection
    error, i.e. exit 2, not a test result. That would make this known-answer
    check report RED on a harness that is working, which is the same class
    of harness fault the file exists to catch.
    """
    return run_pytest(
        str(path), "--rootdir", str(path.parent), "-o", "addopts=",
    )


def test_runner_returns_zero_on_a_file_that_must_pass(tmp_path: Path) -> None:
    res = _run_isolated(_write(tmp_path, "test_known_pass.py", PASSING))
    assert res.code == 0, (
        f"runner reported exit {res.code} on a file that cannot fail. The "
        f"harness itself is broken, so every verdict it has given is void.\n"
        f"{res.tail()}"
    )
    assert res.ok is True


def test_runner_returns_nonzero_on_a_file_that_must_fail(tmp_path: Path) -> None:
    """The direction the broken harnesses got wrong.

    A runner that cannot report failure is a check that cannot fail: it
    would have greenlit both the piped-``tail`` readings this file exists to
    prevent.
    """
    res = _run_isolated(_write(tmp_path, "test_known_fail.py", FAILING))
    assert res.code != 0, (
        "runner reported SUCCESS on a file whose single test asserts 1+1==3. "
        "This is precisely the failure that piping pytest into `tail` and "
        "reading $? produced twice in one session."
    )
    assert res.ok is False


def test_runner_never_uses_a_shell() -> None:
    """The structural property, asserted rather than trusted to a comment.

    ``shell=False`` is what makes piping impossible *inside* the runner. If
    a future edit passes a command string with ``shell=True``, a caller
    could append ``| tail`` and the returned code would stop being
    pytest's -- the exact defect, reintroduced one layer down.
    """
    import ast
    import inspect

    import run_suite

    # AST, not a substring. A `"shell=False" in src` check PASSES on a call
    # that no longer passes the argument, because this function's own
    # docstring contains the words "shell=False" while explaining why it
    # matters. The prose describing the property satisfied the test for the
    # property -- found by mutation M13, which removed the argument and the
    # substring version stayed green.
    tree = ast.parse(inspect.getsource(run_suite._run))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "run"
    ]
    assert len(calls) == 1, f"expected exactly one subprocess.run call, got {len(calls)}"
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert "shell" in kw, (
        "run_suite._run calls subprocess.run WITHOUT an explicit shell=. "
        "Python defaults to shell=False, so this is not yet a live bug -- but "
        "the pin is the thing being asserted, and an implicit default is not "
        "a pin. With a shell, the returned exit code can be a pipeline's "
        "instead of the command's, which is the defect this module exists to "
        "make impossible."
    )
    assert isinstance(kw["shell"], ast.Constant) and kw["shell"].value is False, (
        f"shell= is not the literal False (got {ast.dump(kw['shell'])[:80]})"
    )
    # And the argv must be a list, so there is no command string to append to.
    assert isinstance(calls[0].args[0], ast.Name), (
        "the command must be passed as the argv list variable, not built as a "
        "string -- a string is what makes a pipeline expressible at all"
    )


def test_output_is_data_beside_the_code_not_a_substitute_for_it() -> None:
    """Reading the tail must not require giving up the real exit code.

    The piped readings happened because getting readable output and getting
    the true status felt like a trade-off. Result carries both, so there is
    nothing to trade.
    """
    res = Result(code=1, output="line1\nline2\nline3")
    assert res.tail(2) == "line2\nline3"
    assert res.code == 1, "tail() must not disturb the code"
    assert res.ok is False
