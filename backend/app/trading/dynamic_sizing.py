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

from typing import Literal, Protocol


BalanceTier = Literal["small", "medium", "large", "whale"]


class _SettingsProto(Protocol):
    SIZING_TIER_BOUNDARIES: dict[str, float]


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
