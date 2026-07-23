"""Class-wide regression: forbid ``.isoformat()`` calls in SQL bind dicts.

Every future instance of this bug class must fail CI before it can land.

The bug: asyncpg strict-binds ``TIMESTAMPTZ`` / ``TIMESTAMP`` columns and
rejects ISO strings. Passing ``dt.isoformat()`` as a bind parameter looks
right (it works fine against SQLite TEXT columns and passes unit tests),
but blows up in production the moment the code path hits Postgres.

Documented instances of the bug class:
  #1  2026-05-26  auto_skip response UPDATE (PR-FIX-PR264)
  #2  2026-05-27  live_exit_monitor reconciler (PR #277)
  #3  2026-07-23  symbol_allowlist_refresh SELECT (PR #350 — this one)
  Plus 4 latent instances swept up in the same PR (slippage_guard,
  patterns, ml/export, symbol_performance_snapshots).

Detection: walk the AST of every module under ``backend/app/`` and flag
any ``session.execute(sql, {"key": something.isoformat()})``-shaped call.
The check is scoped to session-driver bind APIs (``execute``, ``fetch``,
``fetchrow``, ``fetchval``) — dict literals passed as the second
positional arg or as ``params=``.

False-positives silenced by design:
  * ``.isoformat()`` inside a dict destined for JSONB / a WS payload / a
    log format arg → NOT flagged (the enclosing call isn't a bind API).
  * ``.isoformat()`` inside a TSTZRANGE canonical literal (e.g.
    ``f"[{a.isoformat()},{b.isoformat()})"``) → also NOT flagged because
    the value in the bind dict is an f-string, not the raw
    ``.isoformat()`` call.

If a false-positive ever needs suppression, prefer moving the value out
of the bind dict rather than adding an exclusion — the whole point is
that a raw ``.isoformat()`` next to a bind key is a code smell.
"""
from __future__ import annotations

import ast
from pathlib import Path


_APP_ROOT = Path(__file__).resolve().parent.parent / "app"
_BIND_METHODS = frozenset({"execute", "fetch", "fetchrow", "fetchval"})


def _is_isoformat_call(node: ast.AST) -> bool:
    """True if ``node`` is a ``<expr>.isoformat(...)`` call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "isoformat"
    )


def _find_bind_dict(node: ast.Call) -> ast.Dict | None:
    """Return the bind dict passed to a bind-API call, or None if the call
    isn't shaped like one.

    Recognises:
      * ``session.execute(sql, {...})``            — positional
      * ``session.execute(sql, params={...})``      — keyword
      * ``conn.fetch(sql, {...})``, etc.
    """
    if not (isinstance(node.func, ast.Attribute)
            and node.func.attr in _BIND_METHODS):
        return None
    # Positional: (sql, params_dict, ...)
    for arg in node.args[1:]:
        if isinstance(arg, ast.Dict):
            return arg
    # Keyword: params=<dict>
    for kw in node.keywords:
        if kw.arg == "params" and isinstance(kw.value, ast.Dict):
            return kw.value
    return None


def _scan_file(py: Path) -> list[str]:
    """Return the list of ``file:line`` violations found in ``py``."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        bind = _find_bind_dict(node)
        if bind is None:
            continue
        for val in bind.values:
            if _is_isoformat_call(val):
                rel = py.relative_to(_APP_ROOT.parent)
                hits.append(f"{rel}:{val.lineno}")
    return hits


def test_no_isoformat_in_execute_params() -> None:
    """CI gate for the ``.isoformat()`` bind-dict bug class.

    Any hit means new code is about to reintroduce the class. Fix by
    binding the raw ``datetime`` object — asyncpg accepts it natively for
    ``TIMESTAMPTZ`` / ``TIMESTAMP`` columns, and SQLAlchemy handles the
    SQLite TEXT coercion transparently.
    """
    violations: list[str] = []
    for py in sorted(_APP_ROOT.rglob("*.py")):
        violations.extend(_scan_file(py))

    assert not violations, (
        "Found `.isoformat()` used as a SQL execute() bind value. asyncpg "
        "strict-binds TIMESTAMPTZ/TIMESTAMP columns and rejects ISO "
        "strings — bind the raw datetime object instead. "
        "Instances:\n  " + "\n  ".join(violations)
    )
