"""The suite cannot reach the internet, and that is enforced not reviewed.

On 2026-10-26 three tests in tests/shadow/ were one review-catch away
from making real Binance calls from CI: they patched a class that had
been deleted, the patch target stopped existing, and they failed with an
AttributeError. Repairing the AttributeError alone would have left the
double unapplied and the call live.

These tests pin the guard in tests/conftest.py. Without them the guard
could be weakened or removed and nothing would notice until the next
near-miss.
"""
from __future__ import annotations

import socket

import pytest


def test_outbound_connect_is_blocked() -> None:
    """A real egress attempt must raise OUR error, not time out.

    Uses a routable-but-dead RFC5737 address so that if the guard were
    absent this would hang-then-fail rather than silently succeed --
    i.e. the test cannot pass for the wrong reason.
    """
    s = socket.socket()
    s.settimeout(0.2)
    try:
        with pytest.raises(RuntimeError, match="OUTBOUND NETWORK BLOCKED"):
            s.connect(("203.0.113.1", 443))
    finally:
        s.close()


def test_create_connection_is_blocked_too() -> None:
    """`socket.create_connection` is the common path in HTTP clients and
    goes through `socket.socket.connect`, so the guard must cover it."""
    with pytest.raises(RuntimeError, match="OUTBOUND NETWORK BLOCKED"):
        socket.create_connection(("203.0.113.1", 443), timeout=0.2)


def test_connect_ex_is_blocked_too() -> None:
    """connect_ex returns an errno instead of raising, so a guard that
    only patched `connect` would leave a silent bypass."""
    s = socket.socket()
    s.settimeout(0.2)
    try:
        with pytest.raises(RuntimeError, match="OUTBOUND NETWORK BLOCKED"):
            s.connect_ex(("203.0.113.1", 443))
    finally:
        s.close()


def test_loopback_is_NOT_blocked() -> None:
    """THE POSITIVE CONTROL.

    A guard that blocked everything would pass all three tests above and
    break the suite: asyncio builds its self-pipe from a socketpair on
    several platforms, and in-process test servers bind loopback. A
    refused connection is fine here -- what must NOT happen is our
    RuntimeError.
    """
    s = socket.socket()
    s.settimeout(0.2)
    try:
        s.connect_ex(("127.0.0.1", 1))  # almost certainly refused; that's OK
    except RuntimeError as e:  # pragma: no cover - the failure we're pinning
        if "OUTBOUND NETWORK BLOCKED" in str(e):
            pytest.fail(
                "the guard blocked LOOPBACK -- this breaks asyncio's "
                "self-pipe and every in-process test server"
            )
        raise
    finally:
        s.close()


@pytest.mark.allow_network
def test_the_allow_network_marker_lifts_the_guard() -> None:
    """THE SECOND POSITIVE CONTROL.

    Without an opt-in, a test that genuinely needs egress would have no
    route except deleting the guard -- and a guard people delete is worse
    than none. This asserts the marker works WITHOUT making a real call:
    the connect fails for ordinary network reasons, and the only thing
    checked is that the failure is not ours.
    """
    s = socket.socket()
    s.settimeout(0.05)
    try:
        with pytest.raises(Exception) as ei:  # noqa: PT011 - any net error
            s.connect(("203.0.113.1", 9))
        assert "OUTBOUND NETWORK BLOCKED" not in str(ei.value), (
            "the allow_network marker did not lift the guard"
        )
    finally:
        s.close()


def test_the_failure_message_names_the_unapplied_double() -> None:
    """The message must carry the narrower lesson, not just the rule.

    "An AttributeError from an unapplied test double is a SYMPTOM;
    repairing the error is not the same as restoring the double." A
    reader who hits this guard right after fixing such an error is
    exactly the person who needs that sentence, so it has to be in the
    message rather than in a doc they will not open.
    """
    from tests.conftest import _SOCKET_BLOCKED_MSG

    low = _SOCKET_BLOCKED_MSG.lower()
    assert "attributeerror" in low, _SOCKET_BLOCKED_MSG
    assert "symptom" in low, _SOCKET_BLOCKED_MSG
    assert "double" in low, _SOCKET_BLOCKED_MSG
    assert "allow_network" in _SOCKET_BLOCKED_MSG
    # And it must say what to check, not merely what went wrong.
    assert "new call path" in low, _SOCKET_BLOCKED_MSG


def test_the_marker_is_registered_so_strict_markers_cannot_silently_pass() -> None:
    """`--strict-markers` is on, so an unregistered marker is an error.

    That is load-bearing: it makes granting network access a visible edit
    in pyproject.toml as well as in the test. If the registration were
    dropped, @pytest.mark.allow_network would start erroring and someone
    would "fix" it by removing the marker from the test -- silently
    re-blocking a test that needs egress, or worse, by deleting the guard.
    """
    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    markers = cfg["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("allow_network:") for m in markers), markers
    addopts = cfg["tool"]["pytest"]["ini_options"]["addopts"]
    assert "--strict-markers" in addopts, addopts
