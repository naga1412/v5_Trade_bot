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


# ────────────────────────────────────────────────────────────────────────────
# PR-FIX-DISPATCH-FLOAT-NOT-ITERABLE (2026-05-26)
# ────────────────────────────────────────────────────────────────────────────
# 2026-05-25 18:00 UTC: WLD/USDT cleared every dispatcher gate at the
# highest entry_score (0.340) and errored with
#   dispatch WLD/USDT/1h -> error: unexpected: argument of type 'float'
#   is not iterable
# Root cause: live_prediction.py:208 merges `pred.prediction_extras`
# (output of predictor._build_extras) into `_layer_payload`. The merged
# keys — static_score, brain_adjust, trap_factor, news_multiplier,
# direction_penalty, final, tier, traps_fired — are floats / strings /
# lists, not layer dicts. _format_layers iterated all entries and
# called `_layer_signed_score(data)` which does `"score" in data`. On
# a float, `"score" in 0.42` raises TypeError. The exception escaped
# to dispatcher.py's outer except, converting outcome to "error" and
# dropping every signal whose prediction_extras were attached.
#
# Until PR #258 (outcome honesty) the dispatcher LIED — reported
# outcome="sent_telegram" even on this error. So this bug had been
# silently killing signals since SP-5 Phase F1 wired the
# `_layer_payload.update(prediction_extras)` merge.
#
# The fix in _format_layers skips non-dict values. The merged metadata
# (scalars / lists / strings) doesn't render as layer rows — it isn't
# a layer.


def test_format_layers_skips_float_metadata_from_prediction_extras() -> None:
    """The actual WLD/USDT 2026-05-25 prod bug repro: a float value in
    layer_summary (from `_build_extras` merged into _layer_payload) must
    NOT raise TypeError: argument of type 'float' is not iterable."""
    cand = _candidate(layer_summary={
        "L1": {"direction": "LONG", "strength": 0.85, "notes": "ok"},
        # prediction_extras-merged scalars — pre-fix these crashed render:
        "static_score": 0.42,
        "brain_adjust": 1.0,
        "trap_factor": 0.95,
        "news_multiplier": 1.1,
        "direction_penalty": 1.0,
        "final": 0.42,
    })
    # Must not raise.
    msg = render_message(cand, leverage=5, now=_NOW)
    # The valid layer renders.
    assert "L1" in msg.body
    assert "+0.85" in msg.body
    # The scalar metadata keys do NOT appear as layer rows (they aren't
    # layers, just metadata). This is the spec-correct behavior — the
    # body's "Layer scores:" section is for layers, not for extras.
    assert "static_score" not in msg.body
    assert "trap_factor" not in msg.body


def test_format_layers_skips_string_metadata() -> None:
    """``tier`` is a string in prediction_extras. Pre-fix `"score" in
    "high"` would have returned False (silently no-op), but the next
    line `data.get("note")` would raise AttributeError on a str.
    Tolerate both."""
    cand = _candidate(layer_summary={
        "L1": {"direction": "LONG", "strength": 0.85, "notes": "ok"},
        "tier": "high",
    })
    msg = render_message(cand, leverage=5, now=_NOW)
    assert "L1" in msg.body
    # `tier` should NOT show up as a layer row.
    assert "tier:" not in msg.body or "L1" in msg.body  # sanity


def test_format_layers_skips_list_metadata_traps_fired() -> None:
    """``traps_fired`` is a list[dict] in prediction_extras. Pre-fix
    `"score" in [{}]` would have returned False (the list contains
    no "score" string), but then `data.get("note")` on a list raises
    AttributeError. Tolerate."""
    cand = _candidate(layer_summary={
        "L1": {"direction": "LONG", "strength": 0.85, "notes": "ok"},
        "traps_fired": [
            {"trap_id": "trap_1", "severity": "high", "side": "LONG"},
            {"trap_id": "trap_2", "severity": "low", "side": "SHORT"},
        ],
    })
    msg = render_message(cand, leverage=5, now=_NOW)
    assert "L1" in msg.body


def test_format_layers_real_production_payload_shape() -> None:
    """Regression guard: build a layer_summary exactly as
    ``live_prediction.py:203-208`` produces in prod — LayerScoreOut
    dicts for L1/L3/L9 + every key from _build_extras merged on top.
    This is the wire-true shape; if the renderer breaks on it, we've
    regressed back to the WLD/USDT silent-drop pattern."""
    real_payload = {
        # Layer dicts (from LayerScoreOut.model_dump()):
        "L1": {
            "direction": "LONG", "strength": 0.65,
            "confidence": 0.7, "notes": "EMAs aligned",
        },
        "L3": {
            "direction": "LONG", "strength": 0.55,
            "confidence": 0.6, "notes": "RSI rising",
        },
        "L9": None,  # news layer abstained
        # _build_extras merged scalars / lists / strings:
        "static_score": 0.42,
        "brain_adjust": 1.0,
        "trap_factor": 1.0,
        "news_multiplier": 1.0,
        "direction_penalty": 1.0,
        "final": 0.42,
        "tier": "high",
        "traps_fired": [],
    }
    cand = _candidate(layer_summary=real_payload)
    # Must not raise.
    msg = render_message(cand, leverage=5, now=_NOW)
    assert "L1" in msg.body
    assert "L3" in msg.body
    assert "(abstained)" in msg.body  # L9 rendered with abstain marker
    assert "+0.65" in msg.body
    # None of the metadata keys leak into the layer section.
    for key in (
        "static_score", "brain_adjust", "trap_factor",
        "news_multiplier", "direction_penalty", "traps_fired",
    ):
        # The key name might appear elsewhere in the body (e.g. funding
        # context), so we check it's not as a layer row prefix.
        assert f"  {key}:" not in msg.body, (
            f"metadata key {key!r} leaked into layer rendering"
        )
