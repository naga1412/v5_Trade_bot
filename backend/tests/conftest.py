import os

import freezegun
import pytest
import socket as _socket

# AF_UNIX is absent on Windows; guard the lookup rather than the use.
_AF_UNIX = getattr(_socket, "AF_UNIX", None)

# freezegun introspects every loaded module's attributes via getattr() when a
# `freeze_time(...)` block enters, which forces transformers' lazy loader to
# import every model class. Several vision models (mllama, detr, ...) chain
# into `transformers.image_utils` for symbols that only exist when optional
# vision deps are installed (PIL, torchvision); without those, the imports
# raise and the whole `with freeze_time(...):` block fails.
#
# Configure freezegun's default ignore list once here — this applies to every
# freeze_time() call in the suite, including ones in test files that don't
# import this conftest directly.
freezegun.configure(default_ignore_list=[
    "transformers",
    "torch",
    "torchvision",
    "PIL",
    "tensorflow",
])


# PR2: `app.config.Settings` has required fields (database_url, redis_url)
# without defaults. CI seeds them via .github/workflows/ci.yml env blocks,
# but bare `pytest backend/tests` from a fresh shell fails at any import
# path that lru-caches `get_settings()`. Pre-seed in-memory defaults at
# conftest import time so the test suite is usable without
# `DATABASE_URL=... REDIS_URL=...` boilerplate every invocation. CI's
# explicit env wins because `setdefault` is a no-op when the var is set.
os.environ.setdefault(
    "DATABASE_URL", "sqlite+aiosqlite:///:memory:",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def empty_prev_hash() -> str:
    return "0" * 64


@pytest.fixture(autouse=True)
def _pause_state_clean(monkeypatch: pytest.MonkeyPatch):
    """Ensure each test starts with a fresh fakeredis-backed pause_state."""
    import fakeredis.aioredis

    from app.ops import pause_state

    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield
    pause_state._reset_for_tests()


@pytest.fixture(autouse=True)
def _cohort_cache_warm(monkeypatch: pytest.MonkeyPatch):
    """Item 0 (2026-08-30): app.shadow.cohort_cache is module-level state,
    None (unwarmed) at process start. Without this fixture, EVERY test
    that opens a shadow position through the real worker.py path would
    hit the "classification cache unavailable" branch -- writing
    symbol_source=NULL and firing a real alert_admin call on every
    single test, drowning out the handful of tests that actually mean
    to exercise that path deliberately.

    Seeds both caches EMPTY (not None) by default: `_classify_cohort`
    then deterministically returns "liquidity_added_spot" for any
    symbol (not in an empty baseline, not in an empty futures_only set)
    -- a real, valid classification, not the failure path. Tests that
    specifically exercise the failure path (empty-cache or raising
    classifier) reset via `cohort_cache._reset_for_tests()` themselves
    to get back to the unwarmed None state; tests that care about a
    SPECIFIC classification seed the caches directly via monkeypatch.
    """
    from app.shadow import cohort_cache

    cohort_cache._reset_for_tests()
    monkeypatch.setattr(cohort_cache, "_baseline_cache", set())
    monkeypatch.setattr(cohort_cache, "_futures_only_cache", set())
    yield
    cohort_cache._reset_for_tests()

# ===========================================================================
# OUTBOUND SOCKETS ARE BLOCKED FOR THE WHOLE SUITE.
# ===========================================================================
# On 2026-10-26 three tests in tests/shadow/ were one review-catch away
# from making REAL BINANCE CALLS from CI. They patched
# `app.shadow.worker.BinanceClient`; that class was deleted with its last
# caller, the patch target stopped existing, and the tests failed with an
# AttributeError. Repairing the AttributeError alone would have left the
# double UNAPPLIED and the call live -- green locally, green in CI, and
# dependent on an external service.
#
# "A careful reader notices" is not a control. This makes it impossible.
#
# BLOCKS CONNECTS, NOT SOCKET CREATION, on purpose: asyncio builds its
# self-pipe from a socketpair on several platforms, so patching
# `socket.socket` itself would stop the event loop from starting and take
# the suite down instead of guarding it. Loopback and AF_UNIX stay open so
# in-process servers, aiosqlite and the event loop are unaffected.
#
# Opt in with @pytest.mark.allow_network when a test genuinely needs
# egress. The marker is registered in pyproject.toml under
# --strict-markers, so granting it is a visible edit in two places.
_REAL_CONNECT = _socket.socket.connect
_REAL_CONNECT_EX = _socket.socket.connect_ex
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "", None}

_SOCKET_BLOCKED_MSG = (
    "OUTBOUND NETWORK BLOCKED IN TESTS: attempted to connect to {addr!r}.\n"
    "\n"
    "A test reaching the real internet is non-deterministic, slow, and\n"
    "fails in any sandboxed runner. Two likely causes:\n"
    "\n"
    "  1. A TEST DOUBLE IS NOT APPLIED. If you arrived here after fixing\n"
    "     an AttributeError from a patch target that no longer exists:\n"
    "     THE AttributeError WAS THE SYMPTOM, NOT THE BUG. Repairing the\n"
    "     error is not the same as restoring the double. Check that the\n"
    "     NEW call path is patched, not just that the old name resolves.\n"
    "  2. The code under test grew a network call it did not have before\n"
    "     (an added cache-refresh or listing-ensure step is the usual\n"
    "     one). Stub the new call as well as the original.\n"
    "\n"
    "If the test genuinely needs egress, mark it\n"
    "@pytest.mark.allow_network -- registered in pyproject.toml."
)


def _host_of(address: object) -> object:
    if isinstance(address, tuple) and address:
        return address[0]
    return address


def _is_local(sock: object, address: object) -> bool:
    fam = getattr(sock, "family", None)
    if _AF_UNIX is not None and fam == _AF_UNIX:
        return True
    host = _host_of(address)
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("utf-8", "replace")
    return host in _LOOPBACK


@pytest.fixture(autouse=True)
def _block_outbound_sockets(request: pytest.FixtureRequest):
    """Autouse: refuse non-loopback connects unless opted in."""
    if request.node.get_closest_marker("allow_network") is not None:
        yield
        return

    def _guard(self, address, *a, **kw):  # noqa: ANN001
        if _is_local(self, address):
            return _REAL_CONNECT(self, address, *a, **kw)
        raise RuntimeError(_SOCKET_BLOCKED_MSG.format(addr=address))

    def _guard_ex(self, address, *a, **kw):  # noqa: ANN001
        if _is_local(self, address):
            return _REAL_CONNECT_EX(self, address, *a, **kw)
        raise RuntimeError(_SOCKET_BLOCKED_MSG.format(addr=address))

    _socket.socket.connect = _guard
    _socket.socket.connect_ex = _guard_ex
    try:
        yield
    finally:
        _socket.socket.connect = _REAL_CONNECT
        _socket.socket.connect_ex = _REAL_CONNECT_EX
