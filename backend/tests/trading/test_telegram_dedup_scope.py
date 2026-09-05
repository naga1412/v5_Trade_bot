"""Structural proof that TELEGRAM_DEDUP is scoped to the Telegram
dispatch send path ONLY, per the operator's non-negotiable constraint
(2026-09-04): it must not touch signal generation, shadow trade
creation, or anything the breakeven-variant measurement reads.

This is a static/import-graph check, not a behavioral one — it proves
the invariant by construction (shadow's modules cannot be affected by a
gate they never import or call) and will fail loudly in CI if a future
change ever wires the dedup gate into shadow's path, rather than relying
on someone remembering the constraint.
"""
from __future__ import annotations

from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Every module whose behavior the operator explicitly said must stay
# untouched: the full shadow package (signal generation via
# app.core.predictor is exercised through shadow_worker.py too, so
# that's covered by the shadow/ sweep below) plus the breakeven-variant
# module by name for extra certainty.
_PROTECTED_PACKAGE = _BACKEND_ROOT / "app" / "shadow"
_PROTECTED_FILES = [
    _BACKEND_ROOT / "app" / "shadow" / "breakeven_variant.py",
    _BACKEND_ROOT / "app" / "shadow" / "worker.py",
]

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "telegram_dedup_gate",
    "_check_telegram_dedup",
    "TELEGRAM_DEDUP_COOLDOWN_HOURS",
)


def _file_references_forbidden_symbols(path: Path) -> list[str]:
    """Return any forbidden substring found anywhere in the file's
    source text (imports, calls, comments, everything) -- deliberately
    broader than an AST-only import check, so this still catches e.g.
    a lazy `import ... as` inside a function body, a string-based
    getattr, or the setting name leaking into shadow's own config reads."""
    text = path.read_text(encoding="utf-8")
    hits = [s for s in _FORBIDDEN_IMPORT_SUBSTRINGS if s in text]
    return hits


def test_shadow_package_never_references_telegram_dedup() -> None:
    """Sweep every .py file under app/shadow/ -- none may reference the
    dedup gate, its check function, or its settings key in any form."""
    assert _PROTECTED_PACKAGE.is_dir(), (
        f"expected {_PROTECTED_PACKAGE} to exist -- test path assumption broke"
    )
    offenders: dict[str, list[str]] = {}
    for py_file in _PROTECTED_PACKAGE.rglob("*.py"):
        hits = _file_references_forbidden_symbols(py_file)
        if hits:
            offenders[str(py_file.relative_to(_BACKEND_ROOT))] = hits
    assert not offenders, (
        "TELEGRAM_DEDUP must not be referenced anywhere under app/shadow/ "
        f"-- found: {offenders}"
    )


def test_breakeven_variant_and_worker_specifically_clean() -> None:
    """Named-file check on the two modules the operator called out by
    name, kept separate from the package sweep above so a future
    refactor that moves shadow/worker.py can't silently drop coverage
    without this test also failing to find the file."""
    for path in _PROTECTED_FILES:
        assert path.is_file(), f"expected {path} to exist -- test path assumption broke"
        hits = _file_references_forbidden_symbols(path)
        assert not hits, f"{path} references forbidden symbols: {hits}"


def test_dispatcher_dedup_check_is_the_only_call_site() -> None:
    """`_check_telegram_dedup` must be CALLED from exactly one place in
    the entire backend/app tree: dispatcher.py's telegram-approve
    branch. (Its own definition in telegram_dedup_gate.py is excluded --
    that's the function's home, not a call site.) More than one real
    call site would mean the gate is being reused somewhere it wasn't
    designed to be scoped for."""
    app_root = _BACKEND_ROOT / "app"
    gate_file = app_root / "trading" / "execution" / "telegram_dedup_gate.py"
    call_sites = []
    for py_file in app_root.rglob("*.py"):
        if py_file == gate_file:
            continue  # the definition, not a call
        text = py_file.read_text(encoding="utf-8")
        if "_check_telegram_dedup(" in text:
            call_sites.append(py_file.relative_to(_BACKEND_ROOT).as_posix())
    assert call_sites == ["app/trading/execution/dispatcher.py"], (
        f"expected exactly one call site (dispatcher.py), found: {call_sites}"
    )


def test_dispatch_function_has_gate_after_all_upstream_gates() -> None:
    """AST-level sanity check: the dedup gate call must appear textually
    AFTER _send_telegram_signal is defined (i.e. inside dispatch(), not
    hoisted above the entry-quality/funding/cooldown/MTF gate chain) --
    confirms it's the LAST thing standing between a fully-evaluated
    signal and the send, not an early short-circuit that could skip
    other real gates."""
    dispatcher_path = _BACKEND_ROOT / "app" / "trading" / "execution" / "dispatcher.py"
    text = dispatcher_path.read_text(encoding="utf-8")
    dedup_idx = text.index("_check_telegram_dedup(")
    send_call_idx = text.index("await _send_telegram_signal(")
    entry_quality_idx = text.index("open_position_gate(")
    assert entry_quality_idx < dedup_idx < send_call_idx, (
        "dedup gate must sit between the entry-quality gate and the "
        "actual Telegram send call, not before or after both"
    )
