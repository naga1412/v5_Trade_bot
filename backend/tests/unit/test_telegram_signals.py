"""SP-8 Phase G — Telegram per-trade signal rendering + callback parsing."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.telegram.signals import (
    ParsedCallback,
    SignalCandidate,
    build_signal_payload,
    parse_callback_data,
    render_message,
)


_NOW = datetime(2026, 5, 4, 14, 23, 0, tzinfo=timezone.utc)


def _candidate(**overrides) -> SignalCandidate:
    base = dict(
        signal_id="abc123",
        symbol="BTC/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=78_250.0,
        stop_loss_price=76_685.0,
        take_profit_price=81_450.0,
        confidence_pct=72,
        layer_summary={
            "L1 macro": {"score": 0.85, "note": "EMAs aligned ascending"},
            "L3 momentum": {"score": 0.72, "note": "RSI 64, MACD hist+"},
            "L5 volume": {"score": 0.40, "note": "1.4× avg volume"},
        },
        margin_usdt=30.0,
        funding_rate_daily=-0.00012,  # -0.012%
        chart_url="https://aji12.nagayuaj.com/tab1/BTC-USDT/1h?signal=abc123",
        sl_distance_pct=0.02,
        rr_ratio=2.05,
    )
    base.update(overrides)
    return SignalCandidate(**base)


# ---- Message rendering ---------------------------------------------------


def test_render_message_includes_spec_section_7_2_fields() -> None:
    """Every line from spec §7.2 must appear in the rendered body."""
    msg = render_message(_candidate(), leverage=5, now=_NOW)
    body = msg.body
    # Header
    assert "LONG" in body
    assert "BTC/USDT" in body
    assert "04 May 2026 14:23 UTC" in body
    # Numbers
    assert "$78,250.00" in body
    assert "$76,685.00" in body
    assert "$81,450.00" in body
    assert "RR ratio:     2.05 : 1" in body
    assert "Confidence:   72%" in body
    # Layer breakdown
    assert "L1 macro" in body
    assert "EMAs aligned ascending" in body
    assert "L3 momentum" in body
    # Position math
    assert "Margin:       $30.00 USDT" in body
    assert "Leverage:     5×" in body
    assert "Position:     $150.00" in body
    # Loss + liquidation
    assert "Loss at SL:" in body
    assert "Liquidation:" in body
    assert "Buffer:" in body
    # Funding (longs receive when negative)
    assert "you receive funding" in body
    # Chart link
    assert "aji12.nagayuaj.com" in body
    # Auto-skip footer
    assert "Auto-skip in 30s" in body


def test_render_message_short_funding_text_inverts() -> None:
    """For SHORT, positive funding means you RECEIVE."""
    msg = render_message(
        _candidate(direction="SHORT", funding_rate_daily=0.005),
        leverage=5, now=_NOW,
    )
    assert "you receive funding" in msg.body


def test_render_message_long_pays_when_funding_positive() -> None:
    msg = render_message(
        _candidate(direction="LONG", funding_rate_daily=0.005),
        leverage=5, now=_NOW,
    )
    assert "you pay funding" in msg.body


def test_render_message_uses_custom_auto_skip_seconds() -> None:
    msg = render_message(_candidate(), leverage=5, auto_skip_seconds=60, now=_NOW)
    assert "Auto-skip in 60s" in msg.body


def test_loss_at_sl_matches_leverage_position_size_math() -> None:
    """Loss at SL = margin × leverage × sl_pct = $30 × 5 × 2% = $3.00."""
    msg = render_message(_candidate(), leverage=5, now=_NOW)
    assert "Loss at SL:   $3.00" in msg.body


# ---- Inline keyboard ----------------------------------------------------


def test_keyboard_has_5_buttons_in_2_rows() -> None:
    msg = render_message(_candidate(), leverage=5, now=_NOW)
    assert len(msg.inline_keyboard) == 2
    assert len(msg.inline_keyboard[0]) == 3  # Approve, +1×, -1×
    assert len(msg.inline_keyboard[1]) == 2  # Custom, Skip


def test_approve_button_text_includes_current_leverage() -> None:
    msg = render_message(_candidate(), leverage=7, now=_NOW)
    btn = msg.inline_keyboard[0][0]
    assert btn["text"] == "✅ Approve 7×"
    assert btn["callback_data"] == "sig:abc123:approve:7"


def test_plus_minus_buttons_clamp_to_1x_and_10x() -> None:
    """+1× at 10 stays at 10; -1× at 1 stays at 1."""
    msg = render_message(_candidate(), leverage=10, now=_NOW)
    plus_btn = msg.inline_keyboard[0][1]
    minus_btn = msg.inline_keyboard[0][2]
    assert plus_btn["callback_data"] == "sig:abc123:adjust:10"
    assert minus_btn["callback_data"] == "sig:abc123:adjust:9"

    msg = render_message(_candidate(), leverage=1, now=_NOW)
    plus_btn = msg.inline_keyboard[0][1]
    minus_btn = msg.inline_keyboard[0][2]
    assert plus_btn["callback_data"] == "sig:abc123:adjust:2"
    assert minus_btn["callback_data"] == "sig:abc123:adjust:1"


# ---- Callback parsing ---------------------------------------------------


def test_parse_approve_callback() -> None:
    p = parse_callback_data("sig:abc123:approve:5")
    assert p == ParsedCallback(signal_id="abc123", action="approve", leverage=5)


def test_parse_adjust_callback() -> None:
    p = parse_callback_data("sig:abc123:adjust:7")
    assert p == ParsedCallback(signal_id="abc123", action="adjust", leverage=7)


def test_parse_skip_callback() -> None:
    p = parse_callback_data("sig:abc123:skip")
    assert p == ParsedCallback(signal_id="abc123", action="skip", leverage=None)


def test_parse_custom_callback() -> None:
    p = parse_callback_data("sig:abc123:custom")
    assert p == ParsedCallback(signal_id="abc123", action="custom", leverage=None)


@pytest.mark.parametrize("bad", [
    "",
    "not-a-callback",
    "sig:",
    "sig::approve:5",         # empty signal_id
    "sig:abc:nope:5",         # unknown action
    "sig:abc:approve",        # approve without leverage
    "sig:abc:approve:abc",    # non-int leverage
    "sig:abc:approve:0",      # leverage below min
    "sig:abc:approve:11",     # leverage above hard cap
    "sig:abc:approve:5:6",    # too many parts
])
def test_parse_callback_returns_none_on_malformed(bad: str) -> None:
    assert parse_callback_data(bad) is None


# ---- Payload serialisation ----------------------------------------------


def test_build_signal_payload_round_trips() -> None:
    import json
    payload = build_signal_payload(
        _candidate(), rendered_at=_NOW, initial_leverage=5,
    )
    j = json.dumps(payload, default=str)
    parsed = json.loads(j)
    assert parsed["signal_id"] == "abc123"
    assert parsed["symbol"] == "BTC/USDT"
    assert parsed["initial_leverage"] == 5
    assert parsed["rendered_at"] == _NOW.isoformat()
    assert parsed["layer_summary"]["L1 macro"]["score"] == 0.85
