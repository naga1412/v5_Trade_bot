"""SP-8 Phase G — per-trade Telegram approval messages.

Spec §7.2: each candidate trade fires a Telegram DM to the user
with the entry/SL/TP/leverage breakdown + inline buttons:

    [ ✅ Approve N× ]   [ +1× ]   [ -1× ]
    [ ⚙ Custom leverage ]   [ ❌ Skip ]

Tapping +1× / -1× re-renders the same message with the leverage
incremented and the dollar-loss / liquidation-buffer math updated.
Custom leverage triggers a reply-prompt. Skip closes the signal.
Auto-skip on §7.4 timeout (default 30s).

This module is pure — it builds message bodies, button keyboards, and
state transitions. The polling worker that posts the message + listens
for callbacks lives in Phase J wiring (extends the SP-4 Phase E
telegram_bot.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.trading.leverage import (
    liquidation_distance_pct,
    loss_at_stop_loss_usdt,
    recommended_leverage,
)


# Fallback used only when Settings lookup fails AND no auto_skip_seconds
# is passed to render_message. The real value comes from
# `Settings.TELEGRAM_APPROVAL_TIMEOUT_SECONDS` (default 600s, per
# PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE, 2026-05-26). Tests
# typically pass `auto_skip_seconds=...` explicitly and bypass settings.
_FALLBACK_AUTO_SKIP_SECONDS = 600
_DEFAULT_HARD_CAP = 10
_MIN_LEVERAGE = 1
# Decoding range for parse_callback_data — intentionally wider than
# `_DEFAULT_HARD_CAP`. The parser's job is to decode what the keyboard
# itself emits; risk policy (the +1× clamp at _DEFAULT_HARD_CAP and the
# initial leverage from `recommended_leverage(hard_cap=user.max_leverage_cap)`)
# lives in `_build_keyboard` and the dispatcher. 125 = Binance Futures
# absolute ceiling, broad enough to never reject a legitimately-emitted
# leverage, narrow enough to still reject obvious garbage.
_PARSER_MAX_LEVERAGE = 125


def _resolved_auto_skip_seconds() -> int:
    """Look up `TELEGRAM_APPROVAL_TIMEOUT_SECONDS` from Settings.

    Lazy import + try/except keeps `app.telegram.signals` importable in
    contexts where the env / pydantic settings aren't fully wired (e.g.
    isolated unit tests that don't set required env vars).
    """
    try:
        from app.config import get_settings
        return int(get_settings().TELEGRAM_APPROVAL_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — fall back to a sensible default
        return _FALLBACK_AUTO_SKIP_SECONDS


@dataclass(frozen=True)
class SignalCandidate:
    """The raw signal data the message renders from."""

    signal_id: str
    symbol: str
    timeframe: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    confidence_pct: float            # 0-100
    # The dict shape is mixed in production because
    # `live_prediction._layer_payload` merges `pred.prediction_extras`
    # (floats: static_score / brain_adjust / trap_factor / news_multiplier
    # / direction_penalty / final; str: tier; list: traps_fired) on top
    # of the per-layer dicts (LayerScoreOut.model_dump() shape) and the
    # abstained-layer None entries. The renderer skips non-dict values
    # (extras) and renders None as "(abstained)" — see `_format_layers`.
    # Typed as `Any` rather than a union because the extras schema is
    # not stable; future PRs may add new keys.
    layer_summary: dict[str, Any]
    margin_usdt: float
    funding_rate_daily: float        # signed; +0.012 means longs pay shorts 1.2%/day
    chart_url: str
    sl_distance_pct: float           # 0.02 = 2%; precomputed by caller
    rr_ratio: float                  # take_profit/stop_loss distance ratio
    # PR2: MTF state at the moment the signal cleared the gate. Persisted
    # in telegram_signals.payload so the approve-time path can populate
    # live_trades.mtf_* (Phase 7) without re-deriving from the prediction
    # row. None for legacy callers / pre-PR2 fixtures.
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions: dict[str, int] | None = None
    # Phase 4 Task 11: three-way ingestion cohort tag (Task 3 addendum).
    # Default matches every other default fixed in Tasks 1/3/9/10 —
    # 'established_top20' is the pre-Phase-4 status quo cohort.
    symbol_source: str = "established_top20"
    qvol_24h: float | None = None
    spread_bps: float | None = None
    depth_0_5pct_usdt: float | None = None


@dataclass(frozen=True)
class RenderedMessage:
    """A Telegram-ready message: body text + inline keyboard."""

    body: str
    inline_keyboard: list[list[dict]]


def _layer_signed_score(data: dict) -> float | None:
    """Read a signed score from a layer dict in either shape.

    Two shapes coexist in callers of render_message:

      * ``{"score": float, "note": str}`` — synthetic shape used by
        ``admin_test_trade.py`` and the original unit-test fixture.
      * ``{"direction": "LONG"/"SHORT"/"NEUTRAL", "strength": float,
        "confidence": float, "notes": str}`` — what
        ``LayerScoreOut.model_dump()`` produces in production
        (via ``live_prediction._layer_payload``).

    Caller MUST pass a ``dict`` — `_format_layers` filters non-dict
    entries upstream so this never sees an extras float/str/list.
    """
    if "score" in data:
        score = data["score"]
        return float(score) if score is not None else None
    strength = data.get("strength")
    if strength is None:
        return None
    direction = data.get("direction")
    if direction == "LONG":
        return float(strength)
    if direction == "SHORT":
        return -float(strength)
    return 0.0  # NEUTRAL


def _format_layers(layers: dict[str, Any]) -> str:
    """Render the per-layer breakdown for the Telegram message body.

    The input dict is mixed-shape because
    ``live_prediction._layer_payload`` first builds
    ``{"1".."10" -> LayerScoreOut.model_dump() | None}`` then merges
    ``pred.prediction_extras`` (``{static_score: float, brain_adjust:
    float, trap_factor: float, news_multiplier: float,
    direction_penalty: float, final: float, tier: str, traps_fired:
    list[dict]}``) on top. The same dict is then re-used for the
    JSONB persistence layer AND threaded into ``SignalCandidate`` for
    rendering — so the renderer has to handle non-dict entries
    gracefully.

    Tolerated failure modes (all uncovered 2026-05-25 by surfacing live
    LONG signals that previously crashed silently):

      1. ``data is None`` — the upstream layer abstained (e.g. L9 news
         returns None when no items match the symbol; L2 patterns stays
         None if pattern_stats_lookup load failed). Rendered as
         ``-- (abstained)``. Pre-fix raised ``AttributeError:
         'NoneType' object has no attribute 'get'``.
      2. ``data`` is not a dict (float / str / list) — prediction_extras
         merge from live_prediction.py. Skipped entirely. Pre-fix raised
         ``TypeError: argument of type 'float' is not iterable``
         (post-#256 surface) or ``AttributeError: 'float' object has
         no attribute 'get'`` (pre-#256 surface). Extras are useful
         metadata for persistence + replay but not for the Telegram
         "Layer scores" UI section, so dropping them from the rendered
         body is semantically correct.
      3. ``LayerScoreOut.model_dump()`` emits ``{direction, strength,
         confidence, notes}`` with no ``"score"`` key — production
         messages had been rendering ``L1: --`` for every layer since
         SP-9 wired L9. Read via ``_layer_signed_score`` which accepts
         both shapes.
    """
    if not layers:
        return "  (no layer scores)"
    lines = []
    for name, data in sorted(layers.items()):
        if data is None:
            lines.append(f"  {name}:    --  (abstained)")
            continue
        if not isinstance(data, dict):
            # prediction_extras merge: static_score (float),
            # brain_adjust (float), tier (str), traps_fired (list),
            # etc. Not layer scores — drop from the rendered "Layer
            # scores" section. Persistence sites still see them via
            # the same dict (this is render-only filtering).
            continue
        score = _layer_signed_score(data)
        note = data.get("note") or data.get("notes", "") or ""
        sign = "+" if score is not None and score >= 0 else ""
        score_str = f"{sign}{score:.2f}" if score is not None else "  --"
        direction_marker = (
            "LONG" if score is not None and score > 0
            else "SHORT" if score is not None and score < 0
            else "—"
        )
        lines.append(
            f"  {name}:  {score_str} ({direction_marker}, {note})"
        )
    return "\n".join(lines)


def _format_funding_text(funding_rate_daily: float, direction: str) -> str:
    """Spec §7.2: funding text mentions whether you pay or receive."""
    pct = funding_rate_daily * 100
    if direction == "LONG":
        if funding_rate_daily > 0:
            return f"{pct:+.3f}% (you pay funding)"
        elif funding_rate_daily < 0:
            return f"{pct:+.3f}% (you receive funding)"
    else:  # SHORT
        if funding_rate_daily > 0:
            return f"{pct:+.3f}% (you receive funding)"
        elif funding_rate_daily < 0:
            return f"{pct:+.3f}% (you pay funding)"
    return f"{pct:+.3f}%"


def _fmt_price(price: float) -> str:
    """Adaptive decimal precision so sub-$1 symbols display sensibly.

    BTC @ $79 000 → "$79,000.00"  (2 dp, comma-grouped)
    SOL @ $150    → "$150.00"
    ONDO @ $0.38  → "$0.380000"   (6 dp — ATR-based SL/TP are visible)
    SHIB @ $0.000015 → "$0.00001500"  (8 dp)
    """
    if price >= 100:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.2f}"
    if price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


def render_message(
    candidate: SignalCandidate,
    *,
    leverage: int,
    auto_skip_seconds: int | None = None,
    now: datetime | None = None,
) -> RenderedMessage:
    """Build the Telegram message body + inline keyboard for spec §7.2.

    `auto_skip_seconds` overrides the configured
    `TELEGRAM_APPROVAL_TIMEOUT_SECONDS` from Settings — when omitted, the
    rendered "Auto-skip in Ns" footer line reflects the env-configured
    value the auto-skip worker is actually enforcing. Tests pass an
    explicit override to assert UI behavior independent of env.
    """
    n = now or datetime.now(timezone.utc)
    if auto_skip_seconds is None:
        auto_skip_seconds = _resolved_auto_skip_seconds()
    direction_emoji = "🔔"
    sl_pct = candidate.sl_distance_pct * 100
    tp_distance = abs(
        candidate.take_profit_price - candidate.entry_price
    ) / candidate.entry_price
    tp_pct = tp_distance * 100

    position_value = candidate.margin_usdt * leverage
    loss_at_sl = loss_at_stop_loss_usdt(
        candidate.margin_usdt, leverage, candidate.sl_distance_pct,
    )
    pct_of_margin = (
        (loss_at_sl / candidate.margin_usdt) * 100
        if candidate.margin_usdt > 0 else 0.0
    )

    liq_distance = liquidation_distance_pct(leverage)
    if candidate.direction == "LONG":
        liq_price = candidate.entry_price * (1 - liq_distance)
    else:
        liq_price = candidate.entry_price * (1 + liq_distance)

    safety_buffer_x = (
        liq_distance / candidate.sl_distance_pct
        if candidate.sl_distance_pct > 0 else 0.0
    )

    max_safe = recommended_leverage(
        margin_usdt=candidate.margin_usdt,
        sl_distance_pct=candidate.sl_distance_pct,
        hard_cap=125,
    )

    cohort_banner = ""
    if candidate.symbol_source == "futures_poll":
        cohort_banner_headline = "🆕 NEW COHORT — thinner liquidity, unvalidated"
    elif candidate.symbol_source == "liquidity_added_spot":
        # Task 11b (ratified 2026-08-19): NOT "thinner liquidity" -- these
        # symbols clear the identical liquidity floor established_top20
        # does. They were excluded from the OLD top-20-by-VOLUME selector
        # on rank alone, not on tradeability. Claiming "thin" here is
        # factually wrong and would train the operator to skip good
        # signals -- see this task's own note above for the real vs.
        # apparent difference between the two new cohorts.
        cohort_banner_headline = "🆕 NEW TO UNIVERSE — liquidity-qualified, performance unvalidated"
    else:
        cohort_banner_headline = None

    if cohort_banner_headline is not None:
        cohort_banner = (
            f"{cohort_banner_headline}\n"
            f"24h vol: ${candidate.qvol_24h:,.0f}  •  "
            f"Spread: {candidate.spread_bps:.1f}bps  •  "
            f"Depth (0.5%): ${candidate.depth_0_5pct_usdt:,.0f}\n"
            f"⚠ Resting depth does not predict depth during a fast move.\n"
            f"─────────────────────────────────────\n"
        )

    body = (
        cohort_banner +
        f"{direction_emoji} {candidate.direction}  •  {candidate.symbol}  "
        f"•  {n.strftime('%d %b %Y %H:%M UTC')}\n"
        f"─────────────────────────────────────\n"
        f"Entry:        ${_fmt_price(candidate.entry_price)}\n"
        f"Stop loss:    ${_fmt_price(candidate.stop_loss_price)}  "
        f"({-sl_pct:+.1f}%)\n"
        f"Take profit:  ${_fmt_price(candidate.take_profit_price)}  "
        f"({tp_pct:+.1f}%)\n"
        f"RR ratio:     {candidate.rr_ratio:.2f} : 1\n"
        f"Confidence:   {candidate.confidence_pct:.0f}%\n"
        f"\n"
        f"Layer scores:\n{_format_layers(candidate.layer_summary)}\n"
        f"─────────────────────────────────────\n"
        f"Margin:       ${candidate.margin_usdt:.2f} USDT\n"
        f"Leverage:     {leverage}× (math: max safe "
        f"{min(max_safe, _DEFAULT_HARD_CAP)}×, capped at "
        f"{_DEFAULT_HARD_CAP}× for risk profile)\n"
        f"Position:     ${position_value:,.2f}\n"
        f"Loss at SL:   ${loss_at_sl:.2f} ({pct_of_margin:.0f}% of margin)\n"
        f"Liquidation:  ${_fmt_price(liq_price)}  "
        f"({-liq_distance*100:+.0f}% adverse)\n"
        f"Buffer:       {safety_buffer_x:.1f}× safety vs SL\n"
        f"Funding rate: "
        f"{_format_funding_text(candidate.funding_rate_daily, candidate.direction)}\n"
        f"─────────────────────────────────────\n"
        f"🔗 View chart on Binance:\n"
        f"{candidate.chart_url}\n"
        f"─────────────────────────────────────\n"
        f"Auto-skip in {auto_skip_seconds}s if no response"
    )

    keyboard = _build_keyboard(candidate.signal_id, leverage)
    return RenderedMessage(body=body, inline_keyboard=keyboard)


def _build_keyboard(signal_id: str, leverage: int) -> list[list[dict]]:
    """Spec §7.2: 5-button inline keyboard layout.

    Row 1:  [ Approve N× ]  [ +1× ]  [ -1× ]
    Row 2:  [ Custom leverage ]  [ Skip ]
    """
    minus_lev = max(_MIN_LEVERAGE, leverage - 1)
    plus_lev = min(_DEFAULT_HARD_CAP, leverage + 1)

    return [
        [
            {
                "text": f"✅ Approve {leverage}×",
                "callback_data": f"sig:{signal_id}:approve:{leverage}",
            },
            {
                "text": "+1×",
                "callback_data": f"sig:{signal_id}:adjust:{plus_lev}",
            },
            {
                "text": "-1×",
                "callback_data": f"sig:{signal_id}:adjust:{minus_lev}",
            },
        ],
        [
            {
                "text": "⚙ Custom leverage",
                "callback_data": f"sig:{signal_id}:custom",
            },
            {
                "text": "❌ Skip",
                "callback_data": f"sig:{signal_id}:skip",
            },
        ],
    ]


# ---- Callback parsing -----------------------------------------------------


@dataclass(frozen=True)
class ParsedCallback:
    """A decoded Telegram callback_query.data payload."""

    signal_id: str
    action: Literal["approve", "adjust", "custom", "skip"]
    leverage: int | None  # set for approve + adjust


def parse_callback_data(data: str) -> ParsedCallback | None:
    """Decode a callback_data string. Returns None on malformed input.

    Format: ``sig:<signal_id>:<action>[:<lev>]``
    """
    if not data or not data.startswith("sig:"):
        return None
    parts = data.split(":")
    if len(parts) < 3 or len(parts) > 4:
        return None
    _, signal_id, action_raw = parts[:3]
    action = action_raw if action_raw in (
        "approve", "adjust", "custom", "skip",
    ) else None
    if action is None or not signal_id:
        return None
    leverage: int | None = None
    if len(parts) == 4:
        try:
            leverage = int(parts[3])
        except ValueError:
            return None
        if leverage < _MIN_LEVERAGE or leverage > _PARSER_MAX_LEVERAGE:
            return None
    if action in ("approve", "adjust") and leverage is None:
        return None
    return ParsedCallback(
        signal_id=signal_id, action=action, leverage=leverage,  # type: ignore[arg-type]
    )


def build_signal_payload(
    candidate: SignalCandidate, *,
    rendered_at: datetime,
    initial_leverage: int,
) -> dict:
    """Serialise a SignalCandidate for the telegram_signals.payload column.

    PR2: includes the 3 MTF state fields. The approve-time path reads
    them from this JSONB so live_trades.mtf_* gets populated when the
    user later approves the trade (matches the auto path's PR2 §4.4
    persistence contract). Pre-PR2 candidates carry None; the JSONB
    keys are still emitted so payload golden tests are stable.
    """
    return {
        "signal_id": candidate.signal_id,
        "symbol": candidate.symbol,
        "timeframe": candidate.timeframe,
        "direction": candidate.direction,
        "entry_price": candidate.entry_price,
        "stop_loss_price": candidate.stop_loss_price,
        "take_profit_price": candidate.take_profit_price,
        "confidence_pct": candidate.confidence_pct,
        "layer_summary": candidate.layer_summary,
        "margin_usdt": candidate.margin_usdt,
        "funding_rate_daily": candidate.funding_rate_daily,
        "chart_url": candidate.chart_url,
        "sl_distance_pct": candidate.sl_distance_pct,
        "rr_ratio": candidate.rr_ratio,
        "rendered_at": rendered_at.isoformat(),
        "initial_leverage": initial_leverage,
        "mtf_agreement": candidate.mtf_agreement,
        "mtf_dominant_tf": candidate.mtf_dominant_tf,
        "mtf_directions": candidate.mtf_directions,
    }


def serialise_payload(payload: dict) -> str:
    """JSON-encode for the JSONB column."""
    return json.dumps(payload, separators=(",", ":"))


__all__ = [
    "ParsedCallback",
    "RenderedMessage",
    "SignalCandidate",
    "build_signal_payload",
    "parse_callback_data",
    "render_message",
    "serialise_payload",
]
