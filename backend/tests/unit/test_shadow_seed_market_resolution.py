"""The shadow seed resolves each symbol's OWN market, and never guesses.

Fifth wrong-endpoint instance (FU-53 class). Before this, the seed asked
Binance SPOT for every symbol, so a futures-only symbol got nothing and
the 400/404 was swallowed at ``log.debug`` -- which is why the
``futures_poll`` cohort has never appeared in shadow at all.

The structural tests here are the durable half: the behavioural ones can
be satisfied by a lucky code path, but "this file contains no spot-default
fetch" is a property.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.shadow import worker as shadow_worker


SRC = pathlib.Path(shadow_worker.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _setup_fn() -> ast.AsyncFunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "setup":
            return node
    raise AssertionError("ShadowWorker.setup not found")


def test_the_seed_no_longer_calls_the_spot_adapter() -> None:
    """No ``*.fetch_klines(...)`` call survives in this module.

    `BinanceClient.fetch_klines` IS the spot default -- cohort-blind, one
    hardcoded host. Asserting its absence is stronger than asserting the
    replacement is present, because a half-migration that left one call
    behind would pass a presence check.
    """
    calls = [
        n for n in ast.walk(TREE)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "fetch_klines"
    ]
    assert not calls, (
        f"{len(calls)} fetch_klines call(s) remain at line(s) "
        f"{[c.lineno for c in calls]}. That function is the spot default; "
        "the seed must resolve each symbol's own market."
    )
    assert "BinanceClient" not in {
        a.asname or a.name
        for n in ast.walk(TREE) if isinstance(n, ast.ImportFrom)
        for a in n.names
    }, "the spot adapter import should be gone with its last caller"


def test_the_seed_uses_the_no_default_resolver() -> None:
    """...and it is the by-symbol resolver, which RAISES rather than
    defaulting. `fetch_bars(symbol_source=...)` would also be correct in
    principle, but the seed has no reliable cohort tag at startup, and a
    wrong cohort tag is how #539 happened."""
    fn = _setup_fn()
    names = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "fetch_bars_by_symbol" in names, sorted(names)


def test_UnresolvedMarket_is_handled_and_is_NOT_swallowed_silently() -> None:
    """The arm exists, is NOT a bare pass, and is NOT below a broad handler.

    Python matches handlers top-down, so an ``except UnresolvedMarket``
    placed after ``except Exception`` is dead code that reads as a fix.
    """
    fn = _setup_fn()
    handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try)
                for h in n.handlers]
    unresolved_idx = []
    broad_idx = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Try):
            continue
        for i, h in enumerate(n.handlers):
            nm = ""
            if isinstance(h.type, ast.Name):
                nm = h.type.id
            elif isinstance(h.type, ast.Attribute):
                nm = h.type.attr
            if nm == "UnresolvedMarket":
                unresolved_idx.append(i)
            elif nm == "Exception" or h.type is None:
                broad_idx.append(i)
    assert unresolved_idx, (
        "no `except UnresolvedMarket` arm in setup() -- the resolver raises, "
        "so an unhandled raise would abort the whole seed on one bad symbol"
    )
    if broad_idx:
        assert min(unresolved_idx) < max(broad_idx), (
            "the UnresolvedMarket arm sits BELOW a broad handler, so it is "
            "dead code: Python matches handlers top-down"
        )
    # The arm must do something, not `pass`/`continue` alone with no record.
    assert "unresolved_syms.add" in SRC, (
        "the UnresolvedMarket arm must RECORD the symbol -- a bare continue "
        "is the silent skip this whole class of defect is made of"
    )


def test_the_unresolved_report_carries_the_SCALE_and_logs_at_ERROR() -> None:
    """One aggregated ERROR naming N of M, not a per-symbol whisper.

    The count is the only datum separating "one freshly listed coin" from
    "the listing cache never loaded", and those need opposite responses.
    A per-symbol line at debug buries exactly that distinction -- which is
    how the pre-migration version hid an entire cohort.
    """
    assert "shadow_worker_seed_market_unresolved" in SRC
    marker = SRC.split("shadow_worker_seed_market_unresolved")[0]
    tail = marker.rstrip()
    assert tail.endswith("log.error(") or "log.error(" in tail[-80:], (
        "the unresolved report must be log.error, not warning/debug"
    )
    assert "%d of %d symbols" in SRC, (
        "the report must name BOTH the failing count and the denominator"
    )
    assert "len(self.symbols)" in SRC, "the denominator must be the real one"


def test_the_listing_cache_is_ENSURED_BEFORE_the_seed_loop() -> None:
    """THE POSITIVE CONTROL, and the thing most likely to be got wrong.

    `resolve_klines_url_for_symbol` answers only by positive listing
    membership. `setup()` runs once at worker start and races
    `futures_only_refresh_task`'s first cycle, so a COLD cache would raise
    for every symbol and seed ZERO buffers -- strictly worse than the
    per-symbol degradation being fixed. Without this ensure-step the
    migration would be a regression that every other test here still
    passes, because they all assert on the unresolved PATH rather than on
    how rarely it should be taken.
    """
    fn = _setup_fn()
    src_lines = SRC.splitlines()
    ensure_line = None
    fetch_line = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            nm = n.func.id if isinstance(n.func, ast.Name) else (
                n.func.attr if isinstance(n.func, ast.Attribute) else "")
            if nm == "refresh_futures_only_cache" and ensure_line is None:
                ensure_line = n.lineno
            if nm == "fetch_bars_by_symbol" and fetch_line is None:
                fetch_line = n.lineno
    assert ensure_line is not None, (
        "setup() must ensure the listing cache; otherwise a cold cache at "
        "worker start raises UnresolvedMarket for EVERY symbol and the "
        "worker seeds nothing"
    )
    assert fetch_line is not None
    assert ensure_line < fetch_line, (
        f"the cache ensure (line {ensure_line}) must come BEFORE the first "
        f"fetch (line {fetch_line}); after it, the race it exists to close "
        f"is still open for the first pass"
    )
    # ...and it must be conditional, not an unconditional refresh on every
    # worker start, which would add a listing round-trip to every restart.
    guard = "\n".join(src_lines[max(0, ensure_line - 6):ensure_line])
    assert "get_futures_only_cache() is None" in guard, (
        "the ensure must be guarded on the cache being unset; an "
        "unconditional refresh costs a round-trip on every restart"
    )


def test_a_cold_cache_failure_does_not_abort_worker_setup() -> None:
    """If the ensure-step itself fails, the seed degrades -- it must not
    raise out of setup() and take the shadow worker down.

    Finds the Try that WRAPS the refresh call, rather than scanning every
    handler in the function. The earlier version of this test did the
    latter and could pass VACUOUSLY when the refresh was not wrapped at
    all -- there was simply nothing to iterate. Locating the wrapping try
    first makes "not wrapped" a failure instead of a silent pass.
    """
    fn = _setup_fn()
    wrapping = None
    for n in ast.walk(fn):
        if not isinstance(n, ast.Try):
            continue
        for inner in ast.walk(n):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "refresh_futures_only_cache"):
                wrapping = n
                break
        if wrapping is not None:
            break
    assert wrapping is not None, (
        "the refresh_futures_only_cache call is not inside a try -- a "
        "network failure at worker start would then propagate out of "
        "setup() and take the shadow worker down entirely"
    )
    assert wrapping.handlers, "the wrapping try has no handler"
    for h in wrapping.handlers:
        body = chr(10).join(ast.unparse(st) for st in h.body)
        assert "raise" not in body, (
            f"the cache-ensure handler re-raises: {body!r}"
        )
        assert "log.error" in body, (
            "the swallow must be loud -- a silent except here is the same "
            f"defect being fixed one layer up: {body!r}"
        )
    assert "listing-cache refresh failed at" in SRC, (
        "the cache-ensure failure must name its consequence, not just fail"
    )


@pytest.mark.parametrize("sym", ["1000PEPE/USDT", "1000SHIB/USDT"])
def test_the_motivating_symbols_are_futures_only_by_naming_convention(
    sym: str,
) -> None:
    """A documentation test, and a real constraint.

    Binance Futures lists `1000PEPEUSDT` / `1000SHIBUSDT` -- the `1000x`
    prefix is a FUTURES contract convention for low-unit-price assets.
    Spot lists `PEPEUSDT` / `SHIBUSDT`. So these two cannot be fetched
    from spot under any circumstances, which is why the pre-migration
    seed could never have worked for them and why they poisoned the
    prediction-validation queue for 53 days.
    """
    base = sym.split("/")[0]
    assert base.startswith("1000"), base
    assert not base[4:].startswith("1000"), base
