"""Trap registry — populated by importing each trap module below.

Phases C and D append concrete trap instances. The orchestrator
`app/core/scoring/run_traps.py:check_all_traps()` iterates this list and
filters by `enabled_set` (per-symbol/per-TF admin disables) at run time.
"""
from app.core.scoring.traps.all_indicator_extreme import AllIndicatorExtremeTrap
from app.core.scoring.traps.alt_btc_indecision import AltBtcIndecisionTrap
from app.core.scoring.traps.base import Trap, TrapContext, TrapFire  # noqa: F401
from app.core.scoring.traps.counter_weekly import CounterWeeklyTrap
from app.core.scoring.traps.friday_weekend import FridayWeekendTrap
from app.core.scoring.traps.liquidity_sweep import LiquiditySweepTrap
from app.core.scoring.traps.parabolic_blowoff import ParabolicBlowoffTrap
from app.core.scoring.traps.pattern_in_pattern import PatternInPatternTrap
from app.core.scoring.traps.pre_news_event import PreNewsEventTrap
from app.core.scoring.traps.price_extreme import PriceExtremeTrap
from app.core.scoring.traps.thin_orderbook import ThinOrderbookTrap
from app.core.scoring.traps.volume_no_followthrough import VolumeNoFollowthroughTrap

ALL_TRAPS: list[Trap] = [
    PreNewsEventTrap(),
    LiquiditySweepTrap(),
    ParabolicBlowoffTrap(),
    FridayWeekendTrap(),
    CounterWeeklyTrap(),
    AllIndicatorExtremeTrap(),
    AltBtcIndecisionTrap(),
    VolumeNoFollowthroughTrap(),
    PatternInPatternTrap(),
    ThinOrderbookTrap(),
    PriceExtremeTrap(),
]
"""Filled by Phase C (12 main traps) + Phase D (5 short-only)."""
