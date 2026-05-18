"""PR9 dynamic sizing — Kelly-fractional × balance tier × hard caps.

Phase 2: classify_balance_tier ships first; the Kelly compute lands in
Phase 3, multi-entry split in Phase 4.

The sizing pipeline (in order):

  1. _resolve_p_win(confidence_pct, settings)
     → returns p_win (real or proxy)
  2. classify_balance_tier(balance_usdt, settings)
     → returns "small" | "medium" | "large" | "whale"
  3. compute_kelly_fraction(p_win, tier, settings)
     → returns fraction of bankroll ∈ [0.0, tier_cap]
  4. compute_dynamic_size(balance, confidence, settings)
     → returns total margin_usdt for the position
  5. split_entries(total, confidence, settings)
     → returns list[float] of tranche sizes (1 elt if no split)

Each function is pure (no DB / no I/O / no side effects). The dispatcher
glue lives in dispatcher.py + multi_entry.py.
"""
from __future__ import annotations

import logging
from typing import Literal, Protocol


log = logging.getLogger(__name__)


BalanceTier = Literal["small", "medium", "large", "whale"]


class _SettingsProto(Protocol):
    SIZING_TIER_BOUNDARIES: dict[str, float]
    SIZING_TIER_MAX_FRACTION: dict[str, float]
    SIZING_FRACTIONAL_KELLY: float
    SIZING_USE_P_WIN_WHEN_AVAILABLE: bool
    DYNAMIC_SIZING_ENABLED: bool
    SIZING_MULTI_ENTRY_THRESHOLD: float
    SIZING_MULTI_ENTRY_RATIOS: list[float]


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


def _resolve_p_win(
    confidence_pct: float, settings: _SettingsProto,
) -> float:
    """Return the probability of a win to feed into Kelly compute.

    PR9 era: always uses `confidence_pct / 100.0` as the proxy.

    PR5 will land an `async predict_p_win(...)` model that returns a
    calibrated probability. Wiring it through requires making this
    function (and its caller `compute_dynamic_size`) async, which
    cascades up to the dispatcher's pre-conditions block. We defer
    that refactoring to PR5 itself.

    `SIZING_USE_P_WIN_WHEN_AVAILABLE` is documented but currently a no-op
    — PR5 flips it on as part of its integration work. Operator can
    inspect the flag in /bot-status/sizing to confirm which path is
    active per env.

    confidence_pct is the 0-100 confidence from the SignalProposal.
    Clamped to [0.0, 1.0] before return.
    """
    # PR5 hook lands here. For now: proxy only.
    _ = settings.SIZING_USE_P_WIN_WHEN_AVAILABLE  # acknowledge the flag exists
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


def compute_dynamic_size(
    *,
    balance_usdt: float,
    confidence_pct: float,
    settings: _SettingsProto,
) -> float | None:
    """Total margin in USDT for the position. Returns None on disabled
    or on any compute error (caller falls back to legacy fixed sizing).

    Fail-open contract: a buggy Kelly compute MUST NOT silently shut
    down trading. Returning None signals the dispatcher to use the
    pre-PR9 path (compute_position_margin).
    """
    if not settings.DYNAMIC_SIZING_ENABLED:
        return None
    try:
        p_win = _resolve_p_win(confidence_pct, settings)
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


def split_entries(
    total_margin_usdt: float,
    confidence_pct: float,
    settings: _SettingsProto,
) -> list[float]:
    """Return tranche sizes for multi-entry placement.

    When `confidence_pct/100 >= SIZING_MULTI_ENTRY_THRESHOLD` (default
    0.75): returns `[total_margin_usdt]` — no split.

    Below the threshold: splits into N tranches per
    `SIZING_MULTI_ENTRY_RATIOS` (default [0.6, 0.4]). Rounding loss
    accumulates in the LAST tranche so `sum(tranches) == total` exactly.

    Invariant: ratios must sum to 1.0. Validated at call time — raises
    ValueError on bad config rather than silently producing a different
    total than the caller asked for.

    total_margin_usdt < 0 raises (defensive — caller passed bad data).
    """
    if total_margin_usdt < 0.0:
        raise ValueError(f"total_margin_usdt must be >= 0; got {total_margin_usdt}")
    confidence = confidence_pct / 100.0
    if confidence >= settings.SIZING_MULTI_ENTRY_THRESHOLD:
        return [total_margin_usdt]

    ratios = settings.SIZING_MULTI_ENTRY_RATIOS
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(
            f"SIZING_MULTI_ENTRY_RATIOS must sum to 1.0; got {sum(ratios)}"
        )

    # Compute all-but-last tranche from ratios; last tranche absorbs
    # rounding so the sum is exact.
    tranches = [total_margin_usdt * r for r in ratios[:-1]]
    tranches.append(total_margin_usdt - sum(tranches))
    return tranches
