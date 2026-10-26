"""Expected price-movement band for the Telegram card.

A VOLATILITY ESTIMATE, NOT A CONVICTION SCORE. The finding behind it is
that a higher `entry_score` buys larger excursions in BOTH directions:
at horizon 10 on the 1h lane the adverse excursion rises +0.2848 R
(clustered SE 0.0330, +8.62 sigma, 1h LONG predictions 60d n=37,682) and
the favourable one +0.3522 R (+3.69 sigma, same population). Asymmetry
1.24x, against the 10.3x that would indicate directional edge. So the
score predicts HOW MUCH, never WHICH WAY.

See `docs/superpowers/plans/2026-10-22-expected-movement-card-scope.md`
for the full scope and
`docs/superpowers/decisions/2026-10-22-a0-escalation-result.md` for the
measurement.

WHY PERCENT AND NOT R. `MAE_R` runs 1.0461 to 1.3309 (1h LONG
predictions 60d, extreme deciles) while the stop sits at exactly 1.0 R
by construction. An R-denominated band printed beside a stop therefore
asserts something that cannot happen to the operator -- they are out at
1.0. The numbers are correct; they are barrier-free path properties,
measured over windows fixed by design with no exit simulation. In
percent the collision disappears, because percent makes no reference to
the stop. R stays the internal unit and is never printed.

WHY THE FIELD IS OMITTED RATHER THAN APPROXIMATED. It renders only where
it was measured: LONG, spot-listed, 1h. A missing field is informative;
a band derived from LONG data shown on a SHORT card is not. There is no
fallback value and no placeholder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- the measured population, fixed at snapshot ------------------------
# Decile boundaries of `final_score`, measured 2026-10-23 over 1h LONG
# `predictions` rows, 60d, n=44,498 (score range 0.0500-0.5642).
#
# DELIBERATELY FIXED, NOT RECOMPUTED ON A ROLLING WINDOW: drifting
# boundaries would need re-validation on every recompute, and the bands
# are coarse enough that boundary precision is nearly irrelevant.
SNAPSHOT_DATE: str = "2026-10-23"
SNAPSHOT_N: int = 44_498
CUT_LOW: float = 0.0675      # 10th percentile -- below this is "narrower than usual"
CUT_HIGH: float = 0.2791     # 90th percentile -- at/above this is "wider than usual"

# Band values in R at horizon 10, from the extreme deciles of the same
# population (1h LONG predictions 60d, n=37,682 resolved). The middle
# band interpolates; it was not measured separately, which is one reason
# the presentation is coarse.
#
# The two legs are kept SEPARATE rather than collapsed into one +/-
# figure because they differ by ~20% and carry very different evidential
# weight: the adverse leg passed the corrected 3.729-sigma bar at +8.62,
# the favourable leg did not (+3.69, SUGGESTIVE). One symmetric number
# would present the unpassed figure with the authority of the passed one.
BAND_R: dict[str, tuple[float, float]] = {
    # label: (favourable_R, adverse_R)
    "narrower than usual": (1.2723, 1.0461),
    "usual": (1.4484, 1.1885),          # midpoint -- interpolated, not measured
    "wider than usual": (1.6245, 1.3309),
}

# Geometry, from app/shadow/engine.py -- the definition the band was
# measured in. The live path's stop is UNCAPPED
# (app/core/predictor.py:92), so R_live = 1.5 * ATR exactly and ATR is
# recoverable as R_live / 1.5. The cap is then re-applied here so the
# band is denominated in the same R as its evidence. On the 1h lane the
# cap binds on 10.48% of trades (LONG shadow trades, n=1,164), which is
# why this matters rather than being pedantic.
SL_ATR_MULT: float = 1.5
SL_CAP_FRAC: float = 0.05

# Render only where the band was measured.
RENDER_DIRECTION: str = "LONG"
RENDER_TIMEFRAME: str = "1h"
RENDER_COHORTS: frozenset[str] = frozenset({"established_top20", "liquidity_added_spot"})

HORIZON_BARS: int = 10
HORIZON_LABEL: str = "~10h"


@dataclass(frozen=True)
class MovementBand:
    """A rendered band. `favourable_pct`/`adverse_pct` are percent of entry."""

    label: str
    favourable_pct: float
    adverse_pct: float


def band_label(entry_score: float) -> str:
    """Comparative statement about WIDTH, never a quality ladder.

    Ladder words (typical / elevated / high) ascend, and anything that
    ascends invites reading the top as the good end -- which is the
    "thinner liquidity, unvalidated" mislabelling inverted. "Wider than
    usual" cannot be misread as "better signal", and "wider" is a
    property of a RANGE, so the two-sidedness is carried by the label.
    """
    if entry_score < CUT_LOW:
        return "narrower than usual"
    if entry_score >= CUT_HIGH:
        return "wider than usual"
    return "usual"


def r_capped(entry_price: float, stop_loss_price: float) -> float | None:
    """R in the definition the band was MEASURED in: min(1.5*ATR, 5% of entry).

    The live stop is uncapped, so `entry - stop` is `1.5 * ATR` exactly
    and ATR is `that / 1.5`. Re-applying the cap here keeps the displayed
    band in the same units as its evidence; using the live uncapped R
    would overstate movement for exactly the high-volatility symbols the
    field exists to flag.

    Returns None when the inputs cannot produce a positive R, so the
    caller omits the field rather than rendering a fabricated number.
    """
    if entry_price <= 0 or stop_loss_price <= 0:
        return None
    r_live = entry_price - stop_loss_price
    if r_live <= 0:
        return None
    atr = r_live / SL_ATR_MULT
    return min(SL_ATR_MULT * atr, SL_CAP_FRAC * entry_price)


def band_for(candidate: Any) -> MovementBand | None:
    """The band for a card, or None when the field must be OMITTED.

    Omission conditions, each deliberate and none of them a fallback:

    - **direction != LONG** -- the measurement is LONG-only and a SHORT
      stream went live 2026-10-23, so this is a live consumer rather
      than a hypothetical.
    - **timeframe != 1h** -- see the note on the 15m guard in the tests:
      currently unreachable, asserted anyway.
    - **cohort is futures_poll** -- FU-53 excluded it from the
      measurement entirely (60 of 279 symbols dropped by a spot-klines
      fetch), so there is no evidence for those symbols.
    - **entry_score missing** -- no score, no decile.
    - **unusable geometry** -- no positive R, no conversion.
    """
    if getattr(candidate, "direction", None) != RENDER_DIRECTION:
        return None
    if getattr(candidate, "timeframe", None) != RENDER_TIMEFRAME:
        return None
    if getattr(candidate, "symbol_source", None) not in RENDER_COHORTS:
        return None
    score = getattr(candidate, "entry_score", None)
    if score is None:
        return None
    entry = getattr(candidate, "entry_price", 0.0) or 0.0
    stop = getattr(candidate, "stop_loss_price", 0.0) or 0.0
    r = r_capped(float(entry), float(stop))
    if r is None:
        return None

    label = band_label(float(score))
    fav_r, adv_r = BAND_R[label]
    return MovementBand(
        label=label,
        favourable_pct=fav_r * r / float(entry) * 100.0,
        adverse_pct=adv_r * r / float(entry) * 100.0,
    )


def render_band(band: MovementBand | None) -> str:
    """The card lines, or "" when the field is omitted.

    NO RESPONSE GUIDANCE. An earlier draft ended with "Consider sizing
    down", which prescribes one response among several when none was
    measured: a wider swing could rationally mean a smaller position, a
    wider stop, or skipping the trade -- and an operator sizing by fixed
    risk over stop distance already has an ATR-driven position size that
    a magnitude flag does not change. State the fact, not the response.
    """
    if band is None:
        return ""
    return (
        f"Price swing, next {HORIZON_LABEL}: "
        f"about +{band.favourable_pct:.1f}% / -{band.adverse_pct:.1f}% of entry "
        f"({band.label})\n"
        "Volatility estimate, not conviction."
    )


__all__ = [
    "BAND_R", "CUT_HIGH", "CUT_LOW", "HORIZON_BARS", "HORIZON_LABEL",
    "RENDER_COHORTS", "RENDER_DIRECTION", "RENDER_TIMEFRAME",
    "SNAPSHOT_DATE", "SNAPSHOT_N",
    "MovementBand", "band_for", "band_label", "r_capped", "render_band",
]
