"""PR9 dynamic sizing — Kelly-fractional × balance tier × hard caps.

The sizing pipeline (in order):

  1. _resolve_p_win(confidence_pct, settings, final_score, direction)
     → returns p_win (calibrated when available, else the proxy)
  2. classify_balance_tier(balance_usdt, settings)
     → returns "small" | "medium" | "large" | "whale"
  3. compute_kelly_fraction(p_win, tier, settings)
     → returns fraction of bankroll ∈ [0.0, tier_cap]
  4. compute_dynamic_size(balance, confidence, settings, final_score, direction)
     → returns total margin_usdt for the position

classify_balance_tier and compute_kelly_fraction are pure (no DB / no
I/O). _resolve_p_win and compute_dynamic_size are async as of the
2026-08-14 remediation work order A2 fix — see _resolve_p_win's
docstring. The dispatcher glue lives in dispatcher.py.

A multi-entry/DCA split-order stage (split_entries + multi_entry.py's
place_multi_entry_orders) was built alongside this but deleted 2026-08
(defect sweep TIER 4): it was never called by the dispatcher, and its
placement path had no equivalent to atomic_placement.place_with_sltp's
real exchange-side STOP_MARKET/TAKE_PROFIT_MARKET protection — wiring it
as designed would have opened lower-confidence positions (the majority,
by construction) with no stop-loss until the next 30s monitor poll. See
git history for the removed implementation if DCA-style entries are
revisited with that gap closed first.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, Protocol


if TYPE_CHECKING:
    from app.core.scoring.types import Direction

log = logging.getLogger(__name__)


BalanceTier = Literal["small", "medium", "large", "whale"]


class _SettingsProto(Protocol):
    SIZING_TIER_BOUNDARIES: dict[str, float]
    SIZING_TIER_MAX_FRACTION: dict[str, float]
    SIZING_FRACTIONAL_KELLY: float
    SIZING_USE_P_WIN_WHEN_AVAILABLE: bool
    DYNAMIC_SIZING_ENABLED: bool


def classify_balance_tier(
    balance_usdt: float, settings: _SettingsProto,
) -> BalanceTier:
    """Bucket a user's bankroll into one of 4 tiers.

    Boundaries (defaults; operator-tunable via SIZING_TIER_BOUNDARIES):
      balance < $1k         → small
      $1k <= balance < $10k → medium
      $10k <= balance < 100k → large
      balance >= $100k       → whale

    Negative or zero balance → small (most conservative; trader
    shouldn't be sizing anyway).
    """
    bounds = settings.SIZING_TIER_BOUNDARIES
    small_max = bounds.get("small_max", 1_000.0)
    medium_max = bounds.get("medium_max", 10_000.0)
    large_max = bounds.get("large_max", 100_000.0)

    if balance_usdt < small_max:
        return "small"
    if balance_usdt < medium_max:
        return "medium"
    if balance_usdt < large_max:
        return "large"
    return "whale"


async def _resolve_p_win(
    confidence_pct: float,
    settings: _SettingsProto,
    *,
    final_score: float | None = None,
    direction: "Direction | str | None" = None,
) -> float:
    """Return the probability of a win to feed into Kelly compute.

    2026-08-14 remediation work order A2 fix: PR5's `predict_p_win`
    calibrated model landed, but this function kept reading
    `SIZING_USE_P_WIN_WHEN_AVAILABLE` into a throwaway variable and
    never actually branching on it -- Kelly sizing had used the raw
    confidence proxy unconditionally regardless of the flag. Fixed:
    when the flag is True AND both `final_score` and `direction` are
    supplied, calls the real calibrated model and uses its result when
    non-None (NEUTRAL direction, no fitted model yet, or a calibration
    failure all return None from predict_p_win -- fall through to the
    proxy in every such case, same fail-open contract as before).

    confidence_pct is the 0-100 confidence from the SignalProposal --
    still the fallback path, and the ONLY path when the caller doesn't
    have a real signal to look up (e.g. the /bot-status/sizing preview
    endpoint, which previews sizing at an arbitrary hypothetical
    confidence with no underlying signal at all). Clamped to [0.0, 1.0]
    before return.
    """
    if (
        settings.SIZING_USE_P_WIN_WHEN_AVAILABLE
        and final_score is not None
        and direction is not None
    ):
        from app.core.scoring.p_win_calibrator import predict_p_win
        from app.core.scoring.types import Direction as _Direction

        resolved_direction = (
            direction if isinstance(direction, _Direction) else _Direction(direction)
        )
        calibrated = await predict_p_win(final_score, resolved_direction)
        if calibrated is not None:
            return calibrated
    return max(0.0, min(1.0, confidence_pct / 100.0))


def compute_kelly_fraction(
    p_win: float, tier: BalanceTier, settings: _SettingsProto,
) -> float:
    """Quarter-Kelly fraction of bankroll for a 1:1 binary outcome.

    Formula (simplified for 1:1 risk:reward, the assumed risk envelope):
      edge       = 2 * p_win - 1
      kelly_pct  = edge          # for 1:1 odds_ratio
      fraction   = kelly_pct × SIZING_FRACTIONAL_KELLY (default 0.25)
      fraction   = clamp(fraction, 0.0, TIER_MAX_FRACTION[tier])

    p_win <= 0.5 → fraction = 0 (no positive edge, no bet).

    Returns a fraction ∈ [0.0, tier_cap]. Caller multiplies by balance
    to get margin_usdt.
    """
    edge = 2.0 * p_win - 1.0
    if edge <= 0.0:
        return 0.0
    raw_kelly = edge * settings.SIZING_FRACTIONAL_KELLY
    tier_cap = settings.SIZING_TIER_MAX_FRACTION.get(tier, 0.01)
    return max(0.0, min(raw_kelly, tier_cap))


async def compute_dynamic_size(
    *,
    balance_usdt: float,
    confidence_pct: float,
    settings: _SettingsProto,
    final_score: float | None = None,
    direction: "Direction | str | None" = None,
) -> float | None:
    """Total margin in USDT for the position. Returns None on disabled
    or on any compute error (caller falls back to legacy fixed sizing).

    `final_score` + `direction` are optional and threaded straight
    through to `_resolve_p_win` -- omit both for the confidence-proxy-
    only preview path (no real signal to calibrate against); pass both
    from the real dispatch path so SIZING_USE_P_WIN_WHEN_AVAILABLE can
    actually take effect (2026-08-14 remediation work order A2).

    Fail-open contract: a buggy Kelly compute MUST NOT silently shut
    down trading. Returning None signals the dispatcher to use the
    pre-PR9 path (compute_position_margin).
    """
    if not settings.DYNAMIC_SIZING_ENABLED:
        return None
    try:
        p_win = await _resolve_p_win(
            confidence_pct, settings, final_score=final_score, direction=direction,
        )
        tier = classify_balance_tier(balance_usdt, settings)
        fraction = compute_kelly_fraction(p_win, tier, settings)
        return balance_usdt * fraction
    except Exception as e:  # noqa: BLE001
        log.error(
            "compute_dynamic_size failed (balance=%s confidence=%s); "
            "falling open to legacy sizing: %s",
            balance_usdt, confidence_pct, e,
        )
        return None
