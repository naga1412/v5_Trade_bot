# backend/tests/unit/test_telegram_signals_cohort.py
from __future__ import annotations

from app.telegram.signals import SignalCandidate, render_message


def _base_kwargs() -> dict:
    return dict(
        signal_id="sig-1", symbol="BTC/USDT", timeframe="1h", direction="LONG",
        entry_price=100.0, stop_loss_price=95.0, take_profit_price=110.0,
        confidence_pct=70.0, layer_summary={}, margin_usdt=50.0,
        funding_rate_daily=0.001, chart_url="https://example.com/chart",
        sl_distance_pct=0.05, rr_ratio=2.0,
    )


def test_established_top20_signal_has_no_cohort_banner() -> None:
    candidate = SignalCandidate(**_base_kwargs(), symbol_source="established_top20")
    rendered = render_message(candidate, leverage=5, auto_skip_seconds=60)
    assert "NEW COHORT" not in rendered.body


def test_liquidity_added_spot_signal_shows_cohort_banner_and_liquidity_numbers() -> None:
    candidate = SignalCandidate(
        **_base_kwargs(), symbol_source="liquidity_added_spot",
        qvol_24h=22_000_000.0, spread_bps=3.5, depth_0_5pct_usdt=60_000.0,
    )
    rendered = render_message(candidate, leverage=5, auto_skip_seconds=60)
    # Task 11b: liquidity_added_spot gets its own headline -- it clears the
    # identical liquidity floor established_top20 does, so it must NOT carry
    # futures_poll's "thinner liquidity" claim.
    assert "NEW TO UNIVERSE" in rendered.body
    assert "liquidity-qualified" in rendered.body.lower()
    assert "thinner liquidity" not in rendered.body.lower()
    assert "22,000,000" in rendered.body or "22000000" in rendered.body
    assert "3.5" in rendered.body
    assert "60,000" in rendered.body or "60000" in rendered.body
    assert "fast move" in rendered.body.lower() or "does not predict" in rendered.body.lower()


def test_futures_poll_signal_shows_cohort_banner_and_liquidity_numbers() -> None:
    candidate = SignalCandidate(
        **_base_kwargs(), symbol_source="futures_poll",
        qvol_24h=25_000_000.0, spread_bps=2.5, depth_0_5pct_usdt=75_000.0,
    )
    rendered = render_message(candidate, leverage=5, auto_skip_seconds=60)
    # Card review #2 (2026-08-20): "thinner liquidity" was factually wrong
    # for futures_poll too -- same class of error as Task 11b's
    # liquidity_added_spot fix, just caught one cohort later. Every
    # symbol here clears the identical liquidity floor; the banner now
    # describes provenance (futures-only listing), not liquidity.
    assert "NEW COHORT" in rendered.body
    assert "futures-only listing" in rendered.body.lower()
    assert "thinner liquidity" not in rendered.body.lower()
    assert "25,000,000" in rendered.body or "25000000" in rendered.body
    assert "2.5" in rendered.body
    assert "75,000" in rendered.body or "75000" in rendered.body
    assert "fast move" in rendered.body.lower() or "does not predict" in rendered.body.lower()


# --- Cohort-banner plumbing regression suite (2026-09-08) -------------
#
# The absence of these tests is why the banner survived un-rendered for
# its entire life. Every test above constructed SignalCandidate with an
# explicit symbol_source, so they all passed while the REAL dispatch
# path never passed one at all -- the dataclass default silently won and
# no card ever carried a banner. These assert on the wiring, not just
# the rendering.

_LIQ = dict(qvol_24h=22_000_000.0, spread_bps=3.5, depth_0_5pct_usdt=60_000.0)


def test_all_three_cohorts_render_correctly_established_has_no_figures() -> None:
    """The three-cohort sweep, with established explicitly figure-less.

    established_top20 never goes through #476's dispatch-time liquidity
    re-check, so it legitimately has NO figures. It must still render --
    it takes render_message's `else` branch and never enters the
    figure-formatting path at all.
    """
    established = SignalCandidate(**_base_kwargs(), symbol_source="established_top20")
    body = render_message(established, leverage=5, auto_skip_seconds=60).body
    assert "NEW COHORT" not in body
    assert "NEW TO UNIVERSE" not in body
    assert "24h vol:" not in body
    assert "LONG" in body and "BTC/USDT" in body  # card still renders

    futures = SignalCandidate(
        **_base_kwargs(), symbol_source="futures_poll", **_LIQ,
    )
    body = render_message(futures, leverage=5, auto_skip_seconds=60).body
    assert "NEW COHORT" in body
    assert "futures-only listing" in body.lower()
    assert "thinner liquidity" not in body.lower()
    assert "22,000,000" in body and "3.5" in body and "60,000" in body

    liq = SignalCandidate(
        **_base_kwargs(), symbol_source="liquidity_added_spot", **_LIQ,
    )
    body = render_message(liq, leverage=5, auto_skip_seconds=60).body
    assert "NEW TO UNIVERSE" in body
    assert "liquidity-qualified" in body.lower()
    assert "thinner liquidity" not in body.lower()
    assert "22,000,000" in body and "3.5" in body and "60,000" in body


def test_new_cohort_without_figures_degrades_instead_of_raising() -> None:
    """A pre-2026-09-08 payload has no liquidity keys.

    The approve/reject edit path rebuilds candidates from those stored
    payloads. Formatting None with ':,.0f' would raise TypeError on
    exactly the new-cohort symbols the banner exists to flag, so the
    headline must survive without the numbers.
    """
    for source, marker in (
        ("futures_poll", "NEW COHORT"),
        ("liquidity_added_spot", "NEW TO UNIVERSE"),
    ):
        candidate = SignalCandidate(**_base_kwargs(), symbol_source=source)
        body = render_message(candidate, leverage=5, auto_skip_seconds=60).body
        assert marker in body          # headline is load-bearing, must survive
        assert "24h vol:" not in body  # figures omitted, not fabricated


def test_payload_roundtrip_preserves_banner_across_re_render() -> None:
    """Guards the vanishing-banner bug: send renders it, edit must too.

    build_signal_payload -> payload -> reconstruct must not lose the
    cohort fields, or the banner appears on the initial card and then
    disappears the moment the operator touches +1x/-1x or approve.
    """
    from datetime import datetime, timezone

    from app.telegram.signals import build_signal_payload

    candidate = SignalCandidate(
        **_base_kwargs(), symbol_source="liquidity_added_spot", **_LIQ,
    )
    payload = build_signal_payload(
        candidate, rendered_at=datetime.now(timezone.utc), initial_leverage=5,
    )
    for key, expected in (
        ("symbol_source", "liquidity_added_spot"),
        ("qvol_24h", 22_000_000.0),
        ("spread_bps", 3.5),
        ("depth_0_5pct_usdt", 60_000.0),
    ):
        assert payload[key] == expected, f"payload lost {key}"

    rebuilt = SignalCandidate(
        **_base_kwargs(),
        symbol_source=payload.get("symbol_source", "established_top20"),
        qvol_24h=payload.get("qvol_24h"),
        spread_bps=payload.get("spread_bps"),
        depth_0_5pct_usdt=payload.get("depth_0_5pct_usdt"),
    )
    body = render_message(rebuilt, leverage=5, auto_skip_seconds=60).body
    assert "NEW TO UNIVERSE" in body
    assert "22,000,000" in body


def test_dispatcher_passes_cohort_fields_to_signal_candidate() -> None:
    """Structural guard on the actual defect.

    The bug was never in render_message -- it was that dispatcher.py
    built SignalCandidate without symbol_source, so the dataclass
    default won on every real card. A rendering test cannot catch that;
    this asserts the call site itself.
    """
    import ast
    import inspect

    from app.trading.execution import dispatcher

    # AST, not string slicing: a naive slice to the next "    )" stops
    # inside the nested _compute_sl_distance_pct(...) call and silently
    # examines only part of the argument list.
    tree = ast.parse(inspect.getsource(dispatcher))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "SignalCandidate"
    ]
    assert calls, "no SignalCandidate(...) call found in dispatcher"

    for call in calls:
        passed = {kw.arg for kw in call.keywords}
        for field in (
            "symbol_source", "qvol_24h", "spread_bps", "depth_0_5pct_usdt",
        ):
            assert field in passed, (
                f"dispatcher's SignalCandidate(...) no longer passes {field} -- "
                "this is the exact defect that kept the cohort banner from "
                "rendering on every real card"
            )
