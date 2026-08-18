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
    assert "NEW COHORT" in rendered.body
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
    assert "NEW COHORT" in rendered.body
    assert "25,000,000" in rendered.body or "25000000" in rendered.body
    assert "2.5" in rendered.body
    assert "75,000" in rendered.body or "75000" in rendered.body
    assert "fast move" in rendered.body.lower() or "does not predict" in rendered.body.lower()
