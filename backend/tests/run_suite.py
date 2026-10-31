"""Run pytest and return ITS exit code. Piping is impossible inside.

WHY THIS EXISTS. Twice in one session a verdict was read from
``$?`` after a shell pipeline:

    # pytest-pipe-ok: the incident this module was built to prevent,
    # quoted below so a reader sees the exact shape to avoid
    python -m pytest ... | tail -45 ; echo "EXIT=$?"     # tail's code, 0
    python -m pytest ... > out.txt 2>&1 ; tail out.txt   # "exited with code 0"

Both printed success while pytest was failing. That is the mirror of the
"check that cannot fail" -- a check that cannot FAIL CORRECTLY, because the
observable being read (a pipeline's status) is not the observable the
question is about (pytest's status). Twice is not a discipline problem, it
is a missing tool.

THE STRUCTURAL PROPERTY: :func:`run` calls ``subprocess.run`` with a
LIST argv and never a shell, so there is no shell to pipe in and no way for
a caller to attach ``| tail`` to the thing whose code is returned. Output is
captured into a string and handed back ALONGSIDE the code, so a caller who
wants to read the tail reads the string -- and the code is still pytest's.

The mutation harness has always worked this way, which is exactly why its
verdicts were never exposed to this. This makes everything else look like
the mutation harness.

Usage, from ``backend/``::

    python tests/run_suite.py tests/unit/test_foo.py -k some_name
    python tests/run_suite.py --lint          # ruff + mypy, as CI runs them
    python tests/run_suite.py --prepush PATHS # lint, then those tests

``--lint`` exists because the other half of the same session's process
failure was pushing without running mypy, which CI runs on the line after
ruff. A pre-push routine that omits a check CI performs is a check that
cannot fail either.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


PYTEST_BASE = ("-p", "no:cacheprovider", "--no-cov", "-q")


@dataclass(frozen=True)
class Result:
    """A command's real exit code, with its output as data beside it."""

    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def tail(self, n: int = 20) -> str:
        return "\n".join(self.output.splitlines()[-n:])


def _run(argv: list[str]) -> Result:
    """Run argv with NO shell. The returned code is this process's own.

    ``shell=False`` is the load-bearing detail: with no shell there is no
    pipeline, so nothing can stand between the command's exit status and
    what this function returns.
    """
    proc = subprocess.run(  # noqa: S603 - argv list, shell=False, no user input
        argv, capture_output=True, text=True, shell=False,
    )
    return Result(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def pytest(*args: str) -> Result:
    """Run pytest with the project's standard flags."""
    return _run([sys.executable, "-m", "pytest", *PYTEST_BASE, *args])


def ruff() -> Result:
    """`ruff check .` -- exactly what CI runs."""
    return _run([sys.executable, "-m", "ruff", "check", "."])


def mypy() -> Result:
    """`mypy app` -- exactly what CI runs, on the line after ruff."""
    return _run([sys.executable, "-m", "mypy", "app"])


def lint() -> list[tuple[str, Result]]:
    """Both CI lint commands, both always run so both get reported."""
    return [("ruff check .", ruff()), ("mypy app", mypy())]


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--lint":
        results = lint()
    elif argv and argv[0] == "--prepush":
        results = lint()
        if all(r.ok for _, r in results):
            results.append(("pytest", pytest(*argv[1:])))
    else:
        results = [("pytest", pytest(*argv))]

    worst = 0
    for name, res in results:
        verdict = "PASS" if res.ok else f"FAIL (exit {res.code})"
        print(f"=== {name}: {verdict} ===")
        if not res.ok:
            print(res.tail(30))
        worst = worst or res.code
    print(f"\nOVERALL: {'PASS' if worst == 0 else f'FAIL (exit {worst})'}")
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
