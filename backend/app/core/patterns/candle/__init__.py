"""Candle pattern registry — extended by individual pattern modules at import.

Phase C populates this. Until then, the list is empty.
"""
from app.core.patterns.base import Pattern

CANDLE_PATTERNS: list[Pattern] = []
from app.core.patterns.candle.hammer import HammerPattern
CANDLE_PATTERNS.append(HammerPattern())
from app.core.patterns.candle.inverted_hammer import InvertedHammerPattern
CANDLE_PATTERNS.append(InvertedHammerPattern())
from app.core.patterns.candle.hanging_man import HangingManPattern
CANDLE_PATTERNS.append(HangingManPattern())
from app.core.patterns.candle.shooting_star import ShootingStarPattern
CANDLE_PATTERNS.append(ShootingStarPattern())
from app.core.patterns.candle.engulfing import EngulfingPattern
CANDLE_PATTERNS.append(EngulfingPattern())
from app.core.patterns.candle.dark_cloud_cover import DarkCloudCoverPattern
CANDLE_PATTERNS.append(DarkCloudCoverPattern())
from app.core.patterns.candle.piercing import PiercingPattern
CANDLE_PATTERNS.append(PiercingPattern())
from app.core.patterns.candle.morning_star import MorningStarPattern
CANDLE_PATTERNS.append(MorningStarPattern())
from app.core.patterns.candle.evening_star import EveningStarPattern
CANDLE_PATTERNS.append(EveningStarPattern())
from app.core.patterns.candle.morning_doji_star import MorningDojiStarPattern
CANDLE_PATTERNS.append(MorningDojiStarPattern())
from app.core.patterns.candle.evening_doji_star import EveningDojiStarPattern
CANDLE_PATTERNS.append(EveningDojiStarPattern())
from app.core.patterns.candle.harami import HaramiPattern
CANDLE_PATTERNS.append(HaramiPattern())
from app.core.patterns.candle.harami_cross import HaramiCrossPattern
CANDLE_PATTERNS.append(HaramiCrossPattern())
from app.core.patterns.candle.tweezer_top import TweezerTopPattern
CANDLE_PATTERNS.append(TweezerTopPattern())
from app.core.patterns.candle.kicking import KickingPattern
CANDLE_PATTERNS.append(KickingPattern())
from app.core.patterns.candle.belt_hold import BeltHoldPattern
CANDLE_PATTERNS.append(BeltHoldPattern())
from app.core.patterns.candle.doji import DojiPattern
CANDLE_PATTERNS.append(DojiPattern())
from app.core.patterns.candle.doji_star import DojiStarPattern
CANDLE_PATTERNS.append(DojiStarPattern())
from app.core.patterns.candle.dragonfly_doji import DragonflyDojiPattern
CANDLE_PATTERNS.append(DragonflyDojiPattern())
from app.core.patterns.candle.gravestone_doji import GravestoneDojiPattern
CANDLE_PATTERNS.append(GravestoneDojiPattern())
from app.core.patterns.candle.long_legged_doji import LongLeggedDojiPattern
CANDLE_PATTERNS.append(LongLeggedDojiPattern())
from app.core.patterns.candle.marubozu import MarubozuPattern
CANDLE_PATTERNS.append(MarubozuPattern())
from app.core.patterns.candle.spinning_top import SpinningTopPattern
CANDLE_PATTERNS.append(SpinningTopPattern())
from app.core.patterns.candle.closing_marubozu import ClosingMarubozuPattern
CANDLE_PATTERNS.append(ClosingMarubozuPattern())
from app.core.patterns.candle.long_line import LongLinePattern
CANDLE_PATTERNS.append(LongLinePattern())
from app.core.patterns.candle.short_line import ShortLinePattern
CANDLE_PATTERNS.append(ShortLinePattern())
from app.core.patterns.candle.high_wave import HighWavePattern
CANDLE_PATTERNS.append(HighWavePattern())
from app.core.patterns.candle.rickshaw_man import RickshawManPattern
CANDLE_PATTERNS.append(RickshawManPattern())
from app.core.patterns.candle.mat_hold import MatHoldPattern
CANDLE_PATTERNS.append(MatHoldPattern())
from app.core.patterns.candle.rise_fall_3_methods import RiseFall3MethodsPattern
CANDLE_PATTERNS.append(RiseFall3MethodsPattern())
from app.core.patterns.candle.separating_lines import SeparatingLinesPattern
CANDLE_PATTERNS.append(SeparatingLinesPattern())
from app.core.patterns.candle.three_white_soldiers import ThreeWhiteSoldiersPattern
CANDLE_PATTERNS.append(ThreeWhiteSoldiersPattern())
from app.core.patterns.candle.three_black_crows import ThreeBlackCrowsPattern
CANDLE_PATTERNS.append(ThreeBlackCrowsPattern())
from app.core.patterns.candle.three_inside_up_down import ThreeInsideUpDownPattern
CANDLE_PATTERNS.append(ThreeInsideUpDownPattern())
from app.core.patterns.candle.three_outside_up_down import ThreeOutsideUpDownPattern
CANDLE_PATTERNS.append(ThreeOutsideUpDownPattern())
from app.core.patterns.candle.three_stars_in_south import ThreeStarsInSouthPattern
CANDLE_PATTERNS.append(ThreeStarsInSouthPattern())
from app.core.patterns.candle.three_line_strike import ThreeLineStrikePattern
CANDLE_PATTERNS.append(ThreeLineStrikePattern())
from app.core.patterns.candle.abandoned_baby import AbandonedBabyPattern
CANDLE_PATTERNS.append(AbandonedBabyPattern())
from app.core.patterns.candle.advance_block import AdvanceBlockPattern
CANDLE_PATTERNS.append(AdvanceBlockPattern())
from app.core.patterns.candle.breakaway import BreakawayPattern
CANDLE_PATTERNS.append(BreakawayPattern())
from app.core.patterns.candle.concealing_baby_swallow import ConcealingBabySwallowPattern
CANDLE_PATTERNS.append(ConcealingBabySwallowPattern())
from app.core.patterns.candle.counterattack import CounterattackPattern
CANDLE_PATTERNS.append(CounterattackPattern())
from app.core.patterns.candle.gap_side_side_white import GapSideSideWhitePattern
CANDLE_PATTERNS.append(GapSideSideWhitePattern())
from app.core.patterns.candle.homing_pigeon import HomingPigeonPattern
CANDLE_PATTERNS.append(HomingPigeonPattern())
from app.core.patterns.candle.identical_three_crows import IdenticalThreeCrowsPattern
CANDLE_PATTERNS.append(IdenticalThreeCrowsPattern())
from app.core.patterns.candle.in_neck import InNeckPattern
CANDLE_PATTERNS.append(InNeckPattern())
from app.core.patterns.candle.kicking_by_length import KickingByLengthPattern
CANDLE_PATTERNS.append(KickingByLengthPattern())
from app.core.patterns.candle.ladder_bottom import LadderBottomPattern
CANDLE_PATTERNS.append(LadderBottomPattern())
from app.core.patterns.candle.on_neck import OnNeckPattern
CANDLE_PATTERNS.append(OnNeckPattern())
from app.core.patterns.candle.stalled_pattern import StalledPatternPattern
CANDLE_PATTERNS.append(StalledPatternPattern())
from app.core.patterns.candle.stick_sandwich import StickSandwichPattern
CANDLE_PATTERNS.append(StickSandwichPattern())
from app.core.patterns.candle.takuri import TakuriPattern
CANDLE_PATTERNS.append(TakuriPattern())
from app.core.patterns.candle.tasuki_gap import TasukiGapPattern
CANDLE_PATTERNS.append(TasukiGapPattern())
from app.core.patterns.candle.thrusting import ThrustingPattern
CANDLE_PATTERNS.append(ThrustingPattern())
