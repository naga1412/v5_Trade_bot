"""Candle pattern registry — extended by individual pattern modules at import.

SP-2 Phase C wires 60 TA-Lib wrappers + 1 composite (``hammer_or_hanging``)
for a total of 61 candle patterns. Each pattern module is imported here and
its instance appended to :data:`CANDLE_PATTERNS`. Hand-rolled "strict" variants
(patterns 62-82 in the plan) are deferred to a follow-up tick.
"""
from app.core.patterns.base import Pattern

# --- batch 1 (reversal) ---
from app.core.patterns.candle.hammer import HammerPattern
from app.core.patterns.candle.inverted_hammer import InvertedHammerPattern
from app.core.patterns.candle.hanging_man import HangingManPattern
from app.core.patterns.candle.shooting_star import ShootingStarPattern
from app.core.patterns.candle.engulfing import EngulfingPattern
from app.core.patterns.candle.dark_cloud_cover import DarkCloudCoverPattern
from app.core.patterns.candle.piercing import PiercingPattern
from app.core.patterns.candle.morning_star import MorningStarPattern
from app.core.patterns.candle.evening_star import EveningStarPattern
from app.core.patterns.candle.morning_doji_star import MorningDojiStarPattern
from app.core.patterns.candle.evening_doji_star import EveningDojiStarPattern
from app.core.patterns.candle.harami import HaramiPattern
from app.core.patterns.candle.harami_cross import HaramiCrossPattern
from app.core.patterns.candle.tweezer_top import TweezerTopPattern
from app.core.patterns.candle.kicking import KickingPattern
from app.core.patterns.candle.belt_hold import BeltHoldPattern

# --- batch 2 (continuation / indecision) ---
from app.core.patterns.candle.doji import DojiPattern
from app.core.patterns.candle.doji_star import DojiStarPattern
from app.core.patterns.candle.dragonfly_doji import DragonflyDojiPattern
from app.core.patterns.candle.gravestone_doji import GravestoneDojiPattern
from app.core.patterns.candle.long_legged_doji import LongLeggedDojiPattern
from app.core.patterns.candle.marubozu import MarubozuPattern
from app.core.patterns.candle.spinning_top import SpinningTopPattern
from app.core.patterns.candle.closing_marubozu import ClosingMarubozuPattern
from app.core.patterns.candle.long_line import LongLinePattern
from app.core.patterns.candle.short_line import ShortLinePattern
from app.core.patterns.candle.high_wave import HighWavePattern
from app.core.patterns.candle.rickshaw_man import RickshawManPattern
from app.core.patterns.candle.mat_hold import MatHoldPattern
from app.core.patterns.candle.rise_fall_3_methods import RiseFall3MethodsPattern
from app.core.patterns.candle.separating_lines import SeparatingLinesPattern

# --- batch 3 (multi-bar reversal) ---
from app.core.patterns.candle.three_white_soldiers import ThreeWhiteSoldiersPattern
from app.core.patterns.candle.three_black_crows import ThreeBlackCrowsPattern
from app.core.patterns.candle.three_inside_up_down import ThreeInsideUpDownPattern
from app.core.patterns.candle.three_outside_up_down import ThreeOutsideUpDownPattern
from app.core.patterns.candle.three_stars_in_south import ThreeStarsInSouthPattern
from app.core.patterns.candle.three_line_strike import ThreeLineStrikePattern
from app.core.patterns.candle.abandoned_baby import AbandonedBabyPattern
from app.core.patterns.candle.advance_block import AdvanceBlockPattern
from app.core.patterns.candle.breakaway import BreakawayPattern
from app.core.patterns.candle.concealing_baby_swallow import ConcealingBabySwallowPattern
from app.core.patterns.candle.counterattack import CounterattackPattern
from app.core.patterns.candle.gap_side_side_white import GapSideSideWhitePattern
from app.core.patterns.candle.homing_pigeon import HomingPigeonPattern
from app.core.patterns.candle.identical_three_crows import IdenticalThreeCrowsPattern
from app.core.patterns.candle.in_neck import InNeckPattern

# --- batch 4 part 1 (advanced + composite) ---
from app.core.patterns.candle.kicking_by_length import KickingByLengthPattern
from app.core.patterns.candle.ladder_bottom import LadderBottomPattern
from app.core.patterns.candle.on_neck import OnNeckPattern
from app.core.patterns.candle.stalled_pattern import StalledPatternPattern
from app.core.patterns.candle.stick_sandwich import StickSandwichPattern
from app.core.patterns.candle.takuri import TakuriPattern
from app.core.patterns.candle.tasuki_gap import TasukiGapPattern
from app.core.patterns.candle.thrusting import ThrustingPattern
from app.core.patterns.candle.two_crows import TwoCrowsPattern
from app.core.patterns.candle.unique_3_river import Unique3RiverPattern
from app.core.patterns.candle.upside_gap_two_crows import UpsideGapTwoCrowsPattern
from app.core.patterns.candle.xside_gap_3_methods import XsideGap3MethodsPattern
from app.core.patterns.candle.hikkake import HikkakePattern
from app.core.patterns.candle.hikkake_modified import HikkakeModifiedPattern
from app.core.patterns.candle.hammer_or_hanging import HammerOrHangingPattern

CANDLE_PATTERNS: list[Pattern] = [
    # batch 1
    HammerPattern(),
    InvertedHammerPattern(),
    HangingManPattern(),
    ShootingStarPattern(),
    EngulfingPattern(),
    DarkCloudCoverPattern(),
    PiercingPattern(),
    MorningStarPattern(),
    EveningStarPattern(),
    MorningDojiStarPattern(),
    EveningDojiStarPattern(),
    HaramiPattern(),
    HaramiCrossPattern(),
    TweezerTopPattern(),
    KickingPattern(),
    BeltHoldPattern(),
    # batch 2
    DojiPattern(),
    DojiStarPattern(),
    DragonflyDojiPattern(),
    GravestoneDojiPattern(),
    LongLeggedDojiPattern(),
    MarubozuPattern(),
    SpinningTopPattern(),
    ClosingMarubozuPattern(),
    LongLinePattern(),
    ShortLinePattern(),
    HighWavePattern(),
    RickshawManPattern(),
    MatHoldPattern(),
    RiseFall3MethodsPattern(),
    SeparatingLinesPattern(),
    # batch 3
    ThreeWhiteSoldiersPattern(),
    ThreeBlackCrowsPattern(),
    ThreeInsideUpDownPattern(),
    ThreeOutsideUpDownPattern(),
    ThreeStarsInSouthPattern(),
    ThreeLineStrikePattern(),
    AbandonedBabyPattern(),
    AdvanceBlockPattern(),
    BreakawayPattern(),
    ConcealingBabySwallowPattern(),
    CounterattackPattern(),
    GapSideSideWhitePattern(),
    HomingPigeonPattern(),
    IdenticalThreeCrowsPattern(),
    InNeckPattern(),
    # batch 4 part 1
    KickingByLengthPattern(),
    LadderBottomPattern(),
    OnNeckPattern(),
    StalledPatternPattern(),
    StickSandwichPattern(),
    TakuriPattern(),
    TasukiGapPattern(),
    ThrustingPattern(),
    TwoCrowsPattern(),
    Unique3RiverPattern(),
    UpsideGapTwoCrowsPattern(),
    XsideGap3MethodsPattern(),
    HikkakePattern(),
    HikkakeModifiedPattern(),
    HammerOrHangingPattern(),
]
