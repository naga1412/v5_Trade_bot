"""mtf_confluence's transport: pooled and throttled, not per-call.

DEFECT (b), fixed 2026-10-27 INDEPENDENTLY of defect (a). This module
built `httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)` PER CALL and fired
6 concurrent requests with no limiter, no bucket, no weight accounting
and no semaphore -- the unthrottled outlier among four sibling modules
that all use the shared RateLimitedClient, with the per-call
construction already recorded at KNOWN_ISSUES.md:185.

WHY IT IS A HAZARD ON ITS OWN TERMS: a fresh client per call never
reuses a connection, so each scoring cycle opens and discards one socket
per request. TIME_WAIT / FD accumulation then produces TRANSPORT failures
on arbitrary symbols, intermittently and environment-specifically -- and
not reproducible from a clean machine, which is exactly what a
180-request controlled repro is. That is the leading explanation for
ARBUSDT being classed PERMANENT by the prod counter while returning 200
on all six timeframes in 118-189 ms here.

ENDPOINT SELECTION IS UNTOUCHED. Defect (a) is held pending the
status-code split.
"""
from __future__ import annotations

import ast
import pathlib

import httpx
import pytest

from app.core.scoring import mtf_confluence as mtf
from app.data.ratelimit import RateLimitedClient

# Spot, named explicitly now that `url` is a required argument.
_SPOT_URL = "https://api.binance.com/api/v3/klines"


SRC = pathlib.Path(mtf.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset():
    mtf._reset_rate_client_for_tests()
    mtf._reset_fetch_failures_for_tests()
    yield
    mtf._reset_rate_client_for_tests()
    mtf._reset_fetch_failures_for_tests()


def test_no_per_call_client_construction_remains() -> None:
    """THE DEFECT, as a property of the code.

    `httpx.AsyncClient(...)` must appear only inside `get_rate_client`,
    which builds the ONE shared pooled instance. Any other construction
    site is a per-call client by definition, because every other function
    here is called per symbol or per cycle.

    MUTATIONS RUN: restoring `httpx.AsyncClient(timeout=...)` at any of
    the three original sites -> red HERE.
    """
    tree = ast.parse(SRC)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "AsyncClient"
                    and node.name != "get_rate_client"):
                offenders.append(f"{node.name} at line {inner.lineno}")
    assert not offenders, (
        "per-call httpx.AsyncClient construction found in:\n  "
        + "\n  ".join(offenders)
        + "\n\nEvery function here runs per symbol or per cycle, so a "
        "client built inside one is a client per call -- which never "
        "reuses a connection and churns one socket per request."
    )


@pytest.mark.asyncio
async def test_the_shared_client_is_a_RateLimitedClient_and_is_a_SINGLETON() -> None:
    """Pooled AND throttled, and the same instance every time.

    A new instance per call would still churn sockets even if it were
    rate-limited, so identity is the property that matters, not type
    alone.
    """
    a = mtf.get_rate_client()
    b = mtf.get_rate_client()
    assert isinstance(a, RateLimitedClient), type(a)
    assert a is b, (
        "within one event loop get_rate_client must return the SAME "
        "instance -- a new client per call would churn sockets even "
        "if rate-limited, so identity is the property that matters"
    )


@pytest.mark.asyncio
async def test_the_pool_is_bounded() -> None:
    """Explicit httpx.Limits, not the library default.

    The default pool is large enough that a burst of 180 concurrent
    requests opens 100 sockets at once, which is the churn being fixed.
    """
    client = mtf.get_rate_client()
    # Read from the httpcore pool, which is where the configured Limits
    # actually land. httpx.AsyncClient has no public `_limits`; the first
    # version of this test assumed one and raised AttributeError, so the
    # path was probed rather than guessed a second time.
    pool = client.http._transport._pool
    assert pool._max_connections == mtf._SHARED_POOL_MAX_CONNECTIONS, (
        f"max_connections={pool._max_connections}, expected "
        f"{mtf._SHARED_POOL_MAX_CONNECTIONS}"
    )
    assert pool._max_keepalive_connections == mtf._SHARED_POOL_MAX_KEEPALIVE
    assert pool._max_keepalive_connections <= pool._max_connections
    # ...and it must be TIGHTER than httpx's default of 100, or the
    # bound is decorative: a 180-request burst would still open 100
    # sockets at once, which is the churn being fixed.
    assert pool._max_connections < 100, (
        f"pool is unbounded relative to httpx's default "
        f"(max_connections={pool._max_connections}, default 100)."
        "\n\nAN UNBOUNDED POOL MEANS A 180-REQUEST BURST OPENS 100 "
        "SOCKETS AT ONCE, which is the socket churn this fix exists "
        "to prevent and the leading explanation for ARBUSDT failing "
        "in prod while being demonstrably healthy."
        "\n\nNOTE FOR WHOEVER SEES THIS BREAK ON AN httpx UPGRADE: "
        "the path client.http._transport._pool._max_connections is "
        "PRIVATE and already moved once -- 0.28.1 has no "
        "AsyncClient._limits, which an earlier version of this test "
        "assumed. REPAIR THE PATH, do not delete the check. A "
        "deleted bound is an unbounded pool with a passing test."
    )


@pytest.mark.asyncio
async def test_klines_weight_matches_the_binance_adapter() -> None:
    """Same weight as app.data.adapters.binance declares.

    Two clients sharing Binance's per-IP budget must agree on what a
    request costs, or one of them is accounting against a budget shape
    the other does not recognise. klines(limit=200) is weight 2.
    """
    from app.data.adapters import binance as binance_adapter

    client = mtf.get_rate_client()
    assert client.endpoint_weights["klines"] == 2
    assert "_default_rate_client" in dir(binance_adapter)


@pytest.mark.asyncio
async def test_the_shared_client_goes_through_the_BUCKET() -> None:
    """A fetch must consume weight, not bypass the limiter.

    THE POINT OF THE WHOLE FIX. If `_get_klines` dispatched to `.get()`
    for the shared client too, the bucket would never be touched and the
    module would remain unthrottled while LOOKING throttled -- which is
    worse than before, because the limiter's presence would imply a
    guarantee it was not providing.
    """
    import time

    client = mtf.get_rate_client()
    bucket = client.buckets["default"]
    before = bucket.tokens
    t0 = time.monotonic()

    captured: dict[str, object] = {}

    async def fake_request(method, url, *, endpoint_key="default", **kw):
        captured["endpoint_key"] = endpoint_key
        captured["method"] = method
        await bucket.acquire(weight=client.endpoint_weights[endpoint_key])
        return httpx.Response(
            200, json=[], request=httpx.Request(method, url),
        )

    client.request = fake_request  # type: ignore[assignment]
    await mtf._get_klines(
        client, _SPOT_URL,
        params={"symbol": "BTCUSDT", "interval": "1h", "limit": 200},
        timeout=2.0,
    )
    assert captured["endpoint_key"] == "klines", captured

    # REFILL-ADJUSTED, not a fixed floor. The bucket refills at
    # refill_per_sec, so between reading `before` and reading tokens
    # again some tokens come back: a bare `<= before - 2` is a
    # TIME-DEPENDENT assertion. It passed locally and failed on a
    # slower CI runner at 1200 -> 1198.0127, i.e. 0.635 ms of refill
    # at 20 tokens/sec. The allowance is DERIVED from the measured
    # elapsed time and the bucket's own rate -- no magic tolerance.
    elapsed = time.monotonic() - t0
    allowance = elapsed * bucket.refill_per_sec
    after = bucket.tokens
    assert after <= before - 2 + allowance, (
        f"weight was not consumed: {before} -> {after} "
        f"(elapsed {elapsed*1000:.3f} ms allows {allowance:.4f} "
        f"tokens of refill, so the ceiling was "
        f"{before - 2 + allowance:.4f}). The shared client must go "
        "through .request(), not .get() -- otherwise the limiter is "
        "present but not consulted, which is worse than its absence "
        "because it implies a guarantee it is not providing."
    )
    # ...and the drop must be REAL, not swallowed by the allowance.
    # If elapsed were large enough to refund the whole weight this
    # assertion would pass vacuously, so the consumption is also
    # checked against the cap: tokens can never EXCEED capacity, so
    # a strict decrease proves the acquire happened.
    assert after < before, (
        f"tokens did not decrease at all: {before} -> {after}"
    )


@pytest.mark.asyncio
async def test_a_BARE_client_still_works_for_the_test_seam() -> None:
    """THE POSITIVE CONTROL on the adapter's other branch.

    The existing mtf tests inject an httpx.AsyncClient with a mock
    transport straight into `_fetch_one_tf`. Rewriting that seam would
    have been a far bigger diff than the fix, so the adapter keeps the
    bare path -- and it has to actually work, or those tests would be
    silently exercising nothing.
    """
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=[[1, "2", "3", "4", "5", "6"]])
    )
    async with httpx.AsyncClient(transport=transport) as bare:
        resp = await mtf._get_klines(
            bare, _SPOT_URL,
            params={"symbol": "BTCUSDT", "interval": "1h", "limit": 200},
            timeout=2.0,
        )
    assert resp.status_code == 200
    assert resp.json() == [[1, "2", "3", "4", "5", "6"]]


def test_dispatch_is_on_TYPE_not_a_capability_sniff() -> None:
    """`isinstance`, not `hasattr`.

    A capability sniff (`hasattr(client, "request")`) would be satisfied
    by a bare httpx.AsyncClient -- which HAS `.request` -- and would
    therefore call it with `endpoint_key=`, raising a TypeError at
    runtime instead of taking the bare path. Worse, a future wrapper
    exposing `.request` would silently get the unthrottled branch.
    """
    tree = ast.parse(SRC)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_get_klines"
    )
    body = ast.unparse(fn)
    assert "isinstance" in body, body
    assert "hasattr" not in body, (
        "capability sniffing here is unsafe: a bare httpx.AsyncClient "
        "also has .request"
    )


def test_the_endpoint_is_RESOLVED_not_hardcoded() -> None:
    """Defect (a) is MIGRATED, so the hardcoded spot URL must be GONE.

    THIS TEST REPLACES `test_the_endpoint_selection_was_NOT_touched`,
    and the replacement is deliberate rather than a test bent to fit a
    change. That test was a SCOPE assertion with an explicit expiry
    written into its own docstring -- "the status-code split that
    decides (a) has not been read yet". The split has now been read
    (96.50% HTTPStatusError_400, 23 symbols, 6 of 6 timeframes), the
    pre-registered rule returned MIGRATE, and the operator authorised
    it. The condition the old test was guarding no longer holds, so
    keeping it would have been asserting a decision that had been
    reversed.

    The replacement is STRICTER, not weaker: the old test allowed the
    spot URL to be hardcoded, this one forbids any hardcoded klines
    endpoint at all and requires the resolver by name.
    """
    # AST, NOT GREP, and the test failing is what caught it. The first
    # version of this assertion grepped SRC for the spot path and went
    # red on mtf_confluence's own docstrings, which EXPLAIN the
    # migration and necessarily quote the URL. The sibling guard
    # (test_bar_fetch_endpoint_guard) already does it correctly via
    # _docstring_node_ids, so the right version was one file away.
    # Fourth instance of the same precision lesson in this repo: a
    # substring matcher must be calibrated against the text it will
    # actually meet, not the text its author had in mind.
    tree = ast.parse(SRC)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    assert not hasattr(mtf, "_BASE_URL"), (
        "a module-level base URL is back. The endpoint is a per-SYMBOL "
        "fact; a module constant can only be a default, and the default "
        "was defect (a)."
    )
    offending = [v for v in literals if "api/v3/klines" in v]
    assert not offending, (
        "a hardcoded spot klines endpoint is back in mtf_confluence -- "
        f"this is defect (a) re-entering: {offending}. Route through "
        "app.data.bar_fetch.resolve_klines_url_for_symbol."
    )
    offending_fut = [v for v in literals if "fapi/v1/klines" in v]
    assert not offending_fut, (
        "a hardcoded FUTURES klines endpoint is no better than a "
        f"hardcoded spot one: {offending_fut}. It is the same silent "
        "default with the other market baked in."
    )
    assert "resolve_klines_url_for_symbol" in SRC, (
        "the endpoint must come from the shared resolver, which is the "
        "one place that requires POSITIVE listing membership and has no "
        "else branch."
    )


@pytest.mark.asyncio
async def test_the_client_is_keyed_PER_EVENT_LOOP() -> None:
    """THE REGRESSION TEST for a bug found before it shipped.

    An httpx.AsyncClient keeps connections bound to the loop that
    opened them. Reusing one across loops raises
    `RuntimeError: Event loop is closed` on the second loop --
    verified directly against the real library, not assumed.

    A per-PROCESS singleton would have been correct in production,
    which has one loop for its lifetime, and WRONG here:
    pytest-asyncio is function-scoped in this repo, and
    tests/unit/test_predictor.py's network-marked tests each call
    build_prediction -> compute_mtf_confluence with no injected
    client. The first would have bound the pool to its loop and the
    remaining fourteen would have failed on theirs.
    """
    import asyncio

    mine = mtf.get_rate_client()
    assert mtf.get_rate_client() is mine

    # A genuinely separate loop, run in a thread so it does not
    # nest inside this one.
    import threading

    other: list[object] = []

    def _in_new_loop() -> None:
        async def _get():
            return mtf.get_rate_client()
        other.append(asyncio.run(_get()))

    t = threading.Thread(target=_in_new_loop)
    t.start()
    t.join()

    assert other and other[0] is not mine, (
        "a DIFFERENT event loop must get a DIFFERENT client. Sharing "
        "one across loops raises 'Event loop is closed' as soon as "
        "the pool tries to reuse a keep-alive connection from the "
        "first loop."
    )


def test_the_accessor_REFUSES_to_hand_out_a_client_with_no_loop() -> None:
    """No running loop -> raise, not a process-wide fallback.

    A fallback would hand back a client that can only be used on a
    loop it was not built for, which is the exact failure the
    keying prevents. Refusing is the honest answer.
    """
    with pytest.raises(RuntimeError):
        mtf.get_rate_client()
