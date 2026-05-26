"""PR-strategy-1 + PR-BOT-INTELLIGENCE-UPGRADE: entry-quality gate.

The shipped PR-strategy-1 gate emits two reasons:

  1. ``DISABLE_SHORT_SIGNALS``: when True, deny every SHORT signal.
  2. ``MIN_ENTRY_SCORE_LONG``: when not None, deny every LONG whose
     ``entry_score`` is below the threshold. ``None`` entry_score (e.g.
     the manual-test-trade path) is treated as "no score available" and
     allowed.

PR-BOT-INTELLIGENCE-UPGRADE adds three more sub-gates. Each is independently
flag-gated and defaults OFF — when all new flags are off, the behaviour is
byte-identical to PR-strategy-1.

  3. ``REGIME_GATE_ENABLED``: deny LONG in confirmed ``bear`` regime and
     SHORT in confirmed ``bull`` regime, **unless** Layer 2 emits a strong
     contrarian pattern (`layer2_direction` matches signal direction AND
     `layer2_confidence >= REGIME_OPPOSITE_PATTERN_OVERRIDE`).
  4. ``PATTERN_BOOST_ENABLED``: when Layer 2 agrees with the signal at
     `layer2_confidence >= PATTERN_BOOST_MIN_CONFIDENCE`, the effective
     score used for `MIN_ENTRY_SCORE_LONG` is `entry_score + PATTERN_BOOST_AMOUNT`.
     When Layer 2 opposes at the same confidence floor, the effective
     score is `entry_score - PATTERN_PENALTY_AMOUNT`.
  5. ``ADX_GATE_ENABLED``: deny when the MTF dominant-TF ADX(14) is below
     `MIN_ADX_TREND_STRENGTH`. Fail-open: if `mtf_adx_by_tf` / `dominant_tf`
     / lookup is missing, the gate passes.

The function is duck-typed: ``signal`` only needs ``.direction`` and
``.entry_score`` plus, optionally, ``.layer2_direction`` /
``.layer2_confidence`` / ``.mtf_adx_by_tf`` / ``.mtf_dominant_tf``. ``Direction``
is compared by ``.value`` (or coerced via ``str()``) so passing a plain string
or the enum both work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AllowDecision:
    """Result of a gate check. ``reason`` is a short stable ID for metrics
    + structured logs; ``None`` when allowed."""

    allow: bool
    reason: str | None = None


def _direction_str(value: Any) -> str:
    """Coerce a direction-shaped value (Direction enum or str) to upper-cased
    string. Defensive against unusual inputs (returns "" → never matches)."""
    if value is None:
        return ""
    inner = getattr(value, "value", value)
    try:
        return str(inner).upper()
    except Exception:  # noqa: BLE001 — defensive fallback
        return ""


def _layer2_agrees_with_signal(
    signal_direction: str,
    signal: Any,
    min_confidence: float,
) -> bool:
    """True when Layer 2 emits a same-direction pattern at >= ``min_confidence``."""
    l2_dir = _direction_str(getattr(signal, "layer2_direction", None))
    l2_conf_raw = getattr(signal, "layer2_confidence", None)
    if l2_dir == "" or l2_conf_raw is None:
        return False
    try:
        l2_conf = float(l2_conf_raw)
    except (TypeError, ValueError):
        return False
    return l2_dir == signal_direction and l2_conf >= min_confidence


def _layer2_opposes_signal(
    signal_direction: str,
    signal: Any,
    min_confidence: float,
) -> bool:
    """True when Layer 2 emits an opposite-direction pattern at >= ``min_confidence``.

    NEUTRAL layer-2 votes do NOT count as opposing (neutral is "no opinion").
    """
    l2_dir = _direction_str(getattr(signal, "layer2_direction", None))
    l2_conf_raw = getattr(signal, "layer2_confidence", None)
    if l2_dir in ("", "NEUTRAL") or l2_conf_raw is None:
        return False
    try:
        l2_conf = float(l2_conf_raw)
    except (TypeError, ValueError):
        return False
    return l2_dir != signal_direction and l2_conf >= min_confidence


def _dominant_tf_adx(signal: Any) -> float | None:
    """Look up ADX of ``signal.mtf_dominant_tf`` in ``signal.mtf_adx_by_tf``.

    Returns ``None`` when any of (mtf_adx_by_tf, mtf_dominant_tf, the per-TF
    entry, the value's numeric coerce) is missing — gate falls open.
    """
    adx_map = getattr(signal, "mtf_adx_by_tf", None)
    dominant = getattr(signal, "mtf_dominant_tf", None)
    if not isinstance(adx_map, dict) or not isinstance(dominant, str):
        return None
    raw = adx_map.get(dominant)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def open_position_gate(
    signal: Any,
    settings: Any,
    *,
    market_regime: str | None = None,
) -> AllowDecision:
    """Decide whether the entry should be allowed through.

    Logic order (significant — earlier checks win so an operator disabling
    SHORTs sees ``short_disabled`` rather than a regime/ADX code):

      1. direction == "SHORT" + DISABLE_SHORT_SIGNALS → deny("short_disabled").
      2. REGIME_GATE_ENABLED + ``market_regime`` opposes direction → deny
         ("blocked_wrong_regime: regime=<r>, direction=<d>"), UNLESS Layer 2
         emits a same-direction pattern at confidence >=
         REGIME_OPPOSITE_PATTERN_OVERRIDE.
      3. ADX_GATE_ENABLED + dominant-TF ADX < MIN_ADX_TREND_STRENGTH →
         deny ("blocked_low_trend_strength: adx=X.X < Y.Y (dominant_tf=Z)").
      4. direction == "LONG": apply optional Layer-2 boost/penalty to the
         entry score, then compare to MIN_ENTRY_SCORE_LONG. ``None``
         entry_score short-circuits to allow (manual operator path).
      5. Otherwise → allow.

    ``market_regime`` is a Literal ``"bull"|"bear"|"neutral"`` (or None when
    REGIME_GATE_ENABLED is off / the fetch failed). The dispatcher fetches
    this via ``app.core.regime.get_cached_market_regime()`` and passes it in.
    Keeping it as a kwarg (not a signal attribute) lets the test suite stub
    regimes without constructing a SignalProposal with that field.
    """
    direction = _direction_str(getattr(signal, "direction", None))

    # ---- 1. SHORT-disable (legacy PR-strategy-1) -----------------------
    if direction == "SHORT" and getattr(settings, "DISABLE_SHORT_SIGNALS", False):
        return AllowDecision(allow=False, reason="short_disabled")

    # ---- 2. Market regime (PR-BOT-INTELLIGENCE-UPGRADE CHANGE 1) -------
    if getattr(settings, "REGIME_GATE_ENABLED", False) and market_regime in ("bull", "bear"):
        opposing = (
            (direction == "LONG" and market_regime == "bear")
            or (direction == "SHORT" and market_regime == "bull")
        )
        if opposing:
            override_floor = float(
                getattr(settings, "REGIME_OPPOSITE_PATTERN_OVERRIDE", 0.8)
            )
            if not _layer2_agrees_with_signal(direction, signal, override_floor):
                return AllowDecision(
                    allow=False,
                    reason=f"blocked_wrong_regime: regime={market_regime}, direction={direction}",
                )

    # ---- 3. Global ADX trend strength (CHANGE 3) -----------------------
    if getattr(settings, "ADX_GATE_ENABLED", False):
        adx = _dominant_tf_adx(signal)
        if adx is not None:
            threshold = float(getattr(settings, "MIN_ADX_TREND_STRENGTH", 25.0))
            if adx < threshold:
                dominant = getattr(signal, "mtf_dominant_tf", None)
                return AllowDecision(
                    allow=False,
                    reason=(
                        f"blocked_low_trend_strength: adx={adx:.1f} < "
                        f"{threshold:.1f} (dominant_tf={dominant})"
                    ),
                )

    # ---- 4. LONG threshold + Layer-2 boost (CHANGE 2) ------------------
    if direction == "LONG":
        long_threshold = getattr(settings, "MIN_ENTRY_SCORE_LONG", None)
        entry_score = getattr(signal, "entry_score", None)
        if long_threshold is not None and entry_score is not None:
            effective_score = float(entry_score)
            boost_detail: str | None = None
            if getattr(settings, "PATTERN_BOOST_ENABLED", False):
                min_conf = float(
                    getattr(settings, "PATTERN_BOOST_MIN_CONFIDENCE", 0.6)
                )
                boost = float(getattr(settings, "PATTERN_BOOST_AMOUNT", 0.10))
                penalty = float(getattr(settings, "PATTERN_PENALTY_AMOUNT", 0.15))
                if _layer2_agrees_with_signal(direction, signal, min_conf):
                    effective_score = min(1.0, effective_score + boost)
                    boost_detail = (
                        f"effective_score={effective_score:.3f}, "
                        f"base={float(entry_score):.3f} + L2_boost={boost:.2f}, "
                        f"L2 conf={float(getattr(signal, 'layer2_confidence', 0.0)):.2f}"
                    )
                elif _layer2_opposes_signal(direction, signal, min_conf):
                    effective_score = max(-1.0, effective_score - penalty)
                    boost_detail = (
                        f"effective_score={effective_score:.3f}, "
                        f"base={float(entry_score):.3f} - L2_penalty={penalty:.2f}, "
                        f"L2 conf={float(getattr(signal, 'layer2_confidence', 0.0)):.2f}"
                    )
            if effective_score < float(long_threshold):
                if boost_detail is not None:
                    return AllowDecision(
                        allow=False,
                        reason=f"below_long_threshold ({boost_detail})",
                    )
                return AllowDecision(allow=False, reason="below_long_threshold")

    return AllowDecision(allow=True, reason=None)
