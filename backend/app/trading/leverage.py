"""SP-8 Phase C — adaptive leverage math.

Compute the largest leverage that keeps the stop-loss safely inside the
liquidation envelope. Spec §6.1.

Wick safety: liquidation distance must be at least 1.25× the stop-loss
distance (equivalently, SL ≤ 80% of liquidation distance). Liquidation
distance ≈ 1 / leverage when fees + funding are ignored — those rounding
errors cost <0.1% per leverage step and are absorbed by the 20% buffer.
"""
from __future__ import annotations


_DEFAULT_HARD_CAP = 10
_SL_SAFETY_BUFFER = 0.80


def recommended_leverage(
    margin_usdt: float,
    sl_distance_pct: float,
    *,
    hard_cap: int = _DEFAULT_HARD_CAP,
) -> int:
    """Return an integer leverage in [1, hard_cap].

    Args:
        margin_usdt: position margin in USDT (used only for the future
            extension where exchange-specific min-notional matters; the
            current formula does not depend on it).
        sl_distance_pct: stop-loss distance from entry as a decimal
            fraction (e.g. ``0.02`` for 2 %).
        hard_cap: maximum leverage to return regardless of math (default
            10×; spec §6.3 allows up to 20× with a warning shown).

    Returns:
        The largest int leverage L such that
            sl_distance_pct ≤ 0.80 / L
        clamped to ``[1, hard_cap]``.

    Edge cases:
        - ``sl_distance_pct ≤ 0`` (zero or negative SL distance): return
          1× as the always-safe fallback.
        - ``hard_cap < 1``: return 1×; degenerate caps are caller error
          but we pick the safe value rather than raise.
        - ``sl_distance_pct ≥ 0.80``: math gives <1×; return 1×.
    """
    if sl_distance_pct <= 0:
        return 1
    if hard_cap < 1:
        return 1

    max_safe = int(_SL_SAFETY_BUFFER / sl_distance_pct)
    return max(1, min(max_safe, hard_cap))


def liquidation_distance_pct(leverage: int) -> float:
    """Return approximate liquidation distance as a decimal fraction.

    Inverse of leverage (ignores fees + funding — same approximation
    used by ``recommended_leverage``). Useful for UI display alongside
    the recommended leverage to show the buffer the user is getting.
    """
    if leverage < 1:
        return 0.0
    return 1.0 / leverage


def loss_at_stop_loss_usdt(
    margin_usdt: float, leverage: int, sl_distance_pct: float,
) -> float:
    """Dollar loss when the stop-loss triggers.

    Loss = position_value × sl_distance_pct
         = (margin × leverage) × sl_distance_pct.

    Useful for the Telegram approval message body (spec §7.2).
    """
    if sl_distance_pct <= 0 or leverage < 1 or margin_usdt <= 0:
        return 0.0
    return margin_usdt * leverage * sl_distance_pct


__all__ = [
    "recommended_leverage",
    "liquidation_distance_pct",
    "loss_at_stop_loss_usdt",
]
