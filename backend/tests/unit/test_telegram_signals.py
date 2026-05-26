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
    # Auto-skip footer — value comes from Settings
    # (TELEGRAM_APPROVAL_TIMEOUT_SECONDS, default 600). The
    # `test_render_message_uses_custom_auto_skip_seconds` test below
    # asserts the explicit-override path; here we just assert the
    # footer line is present.
    assert "Auto-skip in" in body and "s if no response" in body


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
    "sig:abc:approve:126",    # leverage above parser ceiling (125)
    "sig:abc:approve:5:6",    # too many parts
])
def test_parse_callback_returns_none_on_malformed(bad: str) -> None:
    assert parse_callback_data(bad) is None


# ---- Regression tests for PR-FIX-CALLBACK-DATA-PARSER-MISMATCH ----------
# Root cause: parser hard-capped leverage at _DEFAULT_HARD_CAP=10, but
# dispatcher renders signals with leverage = recommended_leverage(
# hard_cap=user.max_leverage_cap), which can be > 10. Operator's signal
# sig_625980a074ef41be (WLD/USDT LONG, 2026-05-25 22:00 UTC) rendered at
# 13× and the Approve button emitted `sig:sig_625980a074ef41be:approve:13`,
# which the parser rejected as "unrecognised callback_data". Fix decouples
# parser decode range (_PARSER_MAX_LEVERAGE=125, Binance ceiling) from
# policy cap (_DEFAULT_HARD_CAP=10, +1× button clamp).


def test_parse_approve_above_old_cap() -> None:
    """Operator's exact failing case: leverage=13 must now decode."""
    p = parse_callback_data("sig:abc:approve:13")
    assert p == ParsedCallback(signal_id="abc", action="approve", leverage=13)


def test_parse_approve_at_new_max() -> None:
    """Parser ceiling is 125 (Binance Futures absolute max)."""
    p = parse_callback_data("sig:abc:approve:125")
    assert p == ParsedCallback(signal_id="abc", action="approve", leverage=125)


def test_parse_approve_above_new_max() -> None:
    """Leverage > 125 still rejected as decode garbage."""
    assert parse_callback_data("sig:abc:approve:126") is None


def test_parse_approve_zero() -> None:
    """Leverage below _MIN_LEVERAGE rejected unchanged."""
    assert parse_callback_data("sig:abc:approve:0") is None


def test_parse_adjust_above_old_cap() -> None:
    """Same fix covers adjust path — adjust uses the same parts[3] branch."""
    p = parse_callback_data("sig:abc:adjust:13")
    assert p == ParsedCallback(signal_id="abc", action="adjust", leverage=13)


def test_parse_skip_unchanged() -> None:
    """3-part skip callback still parses (no leverage)."""
    p = parse_callback_data("sig:abc:skip")
    assert p == ParsedCallback(signal_id="abc", action="skip", leverage=None)


def test_parse_custom_unchanged() -> None:
    """3-part custom callback still parses (no leverage)."""
    p = parse_callback_data("sig:abc:custom")
    assert p == ParsedCallback(signal_id="abc", action="custom", leverage=None)


def test_parse_approve_real_signal_id() -> None:
    """Operator's exact prod callback string from 2026-05-25 22:01 UTC."""
    p = parse_callback_data("sig:sig_625980a074ef41be:approve:13")
    assert p == ParsedCallback(
        signal_id="sig_625980a074ef41be", action="approve", leverage=13,
    )


def test_parse_malformed_too_short() -> None:
    """Truncated callbacks still rejected."""
    assert parse_callback_data("sig:abc") is None


def test_parse_malformed_unknown_action() -> None:
    """Unknown action names still rejected even with valid leverage."""
    assert parse_callback_data("sig:abc:nuke:5") is None


def test_parse_malformed_non_int_leverage() -> None:
    """Non-int leverage still rejected."""
    assert parse_callback_data("sig:abc:approve:high") is None


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


# ---- Regression tests for PR-DISPATCH-RENDER-ABSTAINED-LAYER-FIX --------
#
# 2026-05-25: BNB/USDT and ZEC/USDT were the only two symbols that
# cleared every dispatcher gate at the 12:00 UTC candle close. Both
# errored with "'NoneType' object has no attribute 'get'" because L9
# (news/sentiment) abstained — returning None — and _format_layers
# called .get() on the None entry directly. Separately, the renderer
# read data.get("score") but LayerScoreOut.model_dump() produces
# {direction, strength, confidence, notes} with no "score" key, so
# every Telegram message had rendered "L1: -- " for every layer
# since SP-9 went live. Both bugs are covered below.


def test_format_layers_handles_abstained_none_entry() -> None:
    """An abstained layer (None value) must render as ``(abstained)``
    rather than raising AttributeError. This is the actual prod bug
    that silently dropped BNB/USDT and ZEC/USDT signals."""
    cand = _candidate(layer_summary={
        "L1": {"direction": "LONG", "strength": 0.85, "notes": "ok"},
        "L9": None,  # news layer abstained — no recent items for the symbol
    })
    # Must not raise.
    msg = render_message(cand, leverage=5, now=_NOW)
    assert "(abstained)" in msg.body
    # The abstained layer still names itself in the body so the operator
    # can see which layer abstained.
    assert "L9" in msg.body


def test_format_layers_renders_layer_score_out_model_dump_shape() -> None:
    """Production code paths build layer_summary via
    LayerScoreOut.model_dump() → {direction, strength, confidence, notes}.
    The renderer must read this real shape, not just the synthetic
    {"score", "note"} test fixture shape."""
    cand = _candidate(layer_summary={
        "L1": {
            "direction": "LONG", "strength": 0.85,
            "confidence": 0.70, "notes": "EMAs aligned",
        },
        "L3": {
            "direction": "SHORT", "strength": 0.60,
            "confidence": 0.55, "notes": "RSI overbought",
        },
        "L7": {
            "direction": "NEUTRAL", "strength": 0.0,
            "confidence": 0.5, "notes": "",
        },
    })
    msg = render_message(cand, leverage=5, now=_NOW)
    # LONG layer: positive signed score, "LONG" direction marker
    assert "+0.85" in msg.body
    # SHORT layer: negative signed score, "SHORT" direction marker
    assert "-0.60" in msg.body
    # Notes from the "notes" key (not "note") should appear
    assert "EMAs aligned" in msg.body
    assert "RSI overbought" in msg.body


def test_format_layers_mixed_synthetic_and_model_dump_shapes() -> None:
    """admin_test_trade.py builds layer_summary with the synthetic
    {"score": ..., "note": ...} shape, while live_prediction builds it
    via model_dump(). The renderer must accept both. A test_trade entry
    next to an abstained L9 must not raise."""
    cand = _candidate(layer_summary={
        "L1": {"direction": "LONG", "strength": 0.5, "notes": "ok"},
        "test_trade": {"note": "admin smoke test"},  # synthetic, no score
        "L9": None,  # abstained
    })
    msg = render_message(cand, leverage=5, now=_NOW)
    # synthetic-shape entry with no "score" renders as -- without raising
    assert "test_trade" in msg.body
    assert "(abstained)" in msg.body


# ---- Regression tests for PR-FIX-DISPATCH-FLOAT-NOT-ITERABLE -----------
#
# 2026-05-25 18:00 UTC: WLD/USDT/1h (final_score=0.340) — a LONG signal
# that cleared every dispatcher gate — errored with
# "TypeError: argument of type 'float' is not iterable". Root cause:
# live_prediction._layer_payload merges pred.prediction_extras (float /
# str / list values: static_score, brain_adjust, trap_factor,
# news_multiplier, direction_penalty, final, tier, traps_fired) on top
# of the per-layer dicts. The same dict is then both persisted (where
# extras are useful for replay) and passed to SignalCandidate for
# rendering (where extras crash the layer-shape interpretation). The
# render path now type-checks each value and skips non-dict entries.
#
# The error is a regression-of-shape from PR-DISPATCH-RENDER-ABSTAINED-
# LAYER-FIX (#256) which switched `_format_layers` from
# `data.get("score")` (raises AttributeError on float) to `"score" in
# data` (raises TypeError on float). Pre-#256 the same scenario would
# have also crashed (with a different exception class), so this is also
# a pre-existing latent bug that was masked by the cosmetic "L1: --"
# rendering and by signals that mostly never reached the render path.


def test_format_layers_skips_float_extras_entries() -> None:
    """prediction_extras float values (static_score, brain_adjust, etc.)
    must be skipped from the rendered layer breakdown, NOT raise
    ``TypeError: argument of type 'float' is not iterable``. This is the
    actual prod bug that silently dropped WLD/USDT at 18:00 UTC
    2026-05-25."""
    cand = _candidate(layer_summary={
        "1": {"direction": "LONG", "strength": 0.5, "notes": "ok"},
        "9": None,
        # The exact prediction_extras shape from `_build_extras`:
        "static_score": 0.340,
        "brain_adjust": 1.0,
        "trap_factor": 0.95,
        "news_multiplier": 1.0,
        "direction_penalty": 1.0,
        "final": 0.340,
        "tier": "A",
        "traps_fired": [],
    })
    # Must not raise.
    msg = render_message(cand, leverage=5, now=_NOW)
    # Layer entries still render
    assert "+0.50" in msg.body
    assert "(abstained)" in msg.body
    # Extras are filtered from the "Layer scores" section — they're
    # persistence-time metadata, not user-visible layer scores.
    assert "static_score" not in msg.body
    assert "brain_adjust" not in msg.body
    assert "traps_fired" not in msg.body
    # "tier" appears in other places ("RR ratio", "Confidence", etc.)
    # so we only assert the extras VALUE isn't rendered as a standalone
    # layer entry — a hypothetical render would be "  tier:  --". A
    # bare-word "A" might match the date string, so instead assert no
    # line matches "tier:" with a leading two-space indent.
    assert "  tier:  " not in msg.body


def test_format_layers_handles_full_live_prediction_layer_payload_shape() -> None:
    """End-to-end: build the exact dict shape that
    ``live_prediction._layer_payload`` produces (10 LayerScoreOut dicts
    + 1 None abstention + 8 prediction_extras keys) and confirm the
    renderer survives + emits a clean message body. Future regression
    guard for the dual-use shape of ``layer_summary``."""
    layer_summary = {
        # 10 layers, model_dump shape, mostly populated, one None
        "1": {"direction": "LONG", "strength": 0.6, "confidence": 0.7, "notes": "L1"},
        "2": {"direction": "LONG", "strength": 0.4, "confidence": 0.6, "notes": "L2"},
        "3": {"direction": "SHORT", "strength": 0.2, "confidence": 0.5, "notes": "L3"},
        "4": {"direction": "NEUTRAL", "strength": 0.0, "confidence": 0.5, "notes": ""},
        "5": {"direction": "LONG", "strength": 0.3, "confidence": 0.6, "notes": "L5"},
        "6": {"direction": "LONG", "strength": 0.5, "confidence": 0.7, "notes": "L6"},
        "7": {"direction": "SHORT", "strength": 0.1, "confidence": 0.4, "notes": "L7"},
        "8": {"direction": "LONG", "strength": 0.7, "confidence": 0.8, "notes": "L8"},
        "9": None,  # L9 news abstained (most common case for non-headline alts)
        "10": {"direction": "LONG", "strength": 0.55, "confidence": 0.65, "notes": "L10"},
        # 8 prediction_extras keys (exact shape from `_build_extras`)
        "static_score": 0.42,
        "brain_adjust": 1.0,
        "trap_factor": 0.95,
        "news_multiplier": 1.05,
        "direction_penalty": 1.0,
        "final": 0.42,
        "tier": "B",
        "traps_fired": [],
    }
    cand = _candidate(layer_summary=layer_summary)
    msg = render_message(cand, leverage=5, now=_NOW)
    # Every layer renders (including abstained L9 with "(abstained)" marker)
    for layer_key in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10"):
        # Each layer key appears in the body; some show as signed scores,
        # "9" shows as abstained.
        assert f"  {layer_key}:" in msg.body
    assert "(abstained)" in msg.body  # L9
    # No extras key shows up as a layer line
    for extras_key in (
        "static_score", "brain_adjust", "trap_factor",
        "news_multiplier", "direction_penalty", "traps_fired",
    ):
        assert f"  {extras_key}:" not in msg.body
