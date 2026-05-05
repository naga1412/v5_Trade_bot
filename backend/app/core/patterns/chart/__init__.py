"""Chart pattern registry — extended by individual pattern modules at import.

SP-2 Phase D wires 76 chart patterns split into five batches:
- batch 1 (15): tops + bottoms
- batch 2 (15): continuation triangles + flags
- batch 3 (15): wedges + channels
- batch 4 (15): cup-and-handle + rounded + gaps
- batch 5 (16): complex multi-bar + multi-TF convergence
"""
from app.core.patterns.base import Pattern

# --- batch 1: tops + bottoms ---
from app.core.patterns.chart.double_top import DoubleTopPattern
from app.core.patterns.chart.double_bottom import DoubleBottomPattern
from app.core.patterns.chart.triple_top import TripleTopPattern
from app.core.patterns.chart.triple_bottom import TripleBottomPattern
from app.core.patterns.chart.head_and_shoulders import HeadAndShouldersPattern
from app.core.patterns.chart.inverse_head_and_shoulders import (
    InverseHeadAndShouldersPattern,
)
from app.core.patterns.chart.v_top import VTopPattern
from app.core.patterns.chart.v_bottom import VBottomPattern
from app.core.patterns.chart.island_reversal_top import IslandReversalTopPattern
from app.core.patterns.chart.island_reversal_bottom import (
    IslandReversalBottomPattern,
)
from app.core.patterns.chart.key_reversal_high import KeyReversalHighPattern
from app.core.patterns.chart.key_reversal_low import KeyReversalLowPattern
from app.core.patterns.chart.bump_and_run_top import BumpAndRunTopPattern
from app.core.patterns.chart.bump_and_run_bottom import BumpAndRunBottomPattern
from app.core.patterns.chart.rounding_top import RoundingTopPattern

# --- batch 2: triangles + flags ---
from app.core.patterns.chart.ascending_triangle import AscendingTrianglePattern
from app.core.patterns.chart.descending_triangle import DescendingTrianglePattern
from app.core.patterns.chart.symmetrical_triangle import SymmetricalTrianglePattern
from app.core.patterns.chart.bullish_flag import BullishFlagPattern
from app.core.patterns.chart.bearish_flag import BearishFlagPattern
from app.core.patterns.chart.bullish_pennant import BullishPennantPattern
from app.core.patterns.chart.bearish_pennant import BearishPennantPattern
from app.core.patterns.chart.rectangle_continuation import (
    RectangleContinuationPattern,
)
from app.core.patterns.chart.expanding_triangle import ExpandingTrianglePattern
from app.core.patterns.chart.broadening_top import BroadeningTopPattern
from app.core.patterns.chart.broadening_bottom import BroadeningBottomPattern
from app.core.patterns.chart.diamond_top import DiamondTopPattern
from app.core.patterns.chart.diamond_bottom import DiamondBottomPattern
from app.core.patterns.chart.channel_up import ChannelUpPattern
from app.core.patterns.chart.channel_down import ChannelDownPattern

# --- batch 3: wedges + channels ---
from app.core.patterns.chart.rising_wedge import RisingWedgePattern
from app.core.patterns.chart.falling_wedge import FallingWedgePattern
from app.core.patterns.chart.ending_diagonal_top import EndingDiagonalTopPattern
from app.core.patterns.chart.ending_diagonal_bottom import (
    EndingDiagonalBottomPattern,
)
from app.core.patterns.chart.regression_channel_up import (
    RegressionChannelUpPattern,
)
from app.core.patterns.chart.regression_channel_down import (
    RegressionChannelDownPattern,
)
from app.core.patterns.chart.parallel_channel import ParallelChannelPattern
from app.core.patterns.chart.andrews_pitchfork import AndrewsPitchforkPattern
from app.core.patterns.chart.fibonacci_retracement_50 import (
    FibonacciRetracement50Pattern,
)
from app.core.patterns.chart.fibonacci_retracement_618 import (
    FibonacciRetracement618Pattern,
)
from app.core.patterns.chart.gann_fan_signal import GannFanSignalPattern
from app.core.patterns.chart.mid_term_consolidation import (
    MidTermConsolidationPattern,
)
from app.core.patterns.chart.breakout_pullback_long import (
    BreakoutPullbackLongPattern,
)
from app.core.patterns.chart.breakout_pullback_short import (
    BreakoutPullbackShortPattern,
)
from app.core.patterns.chart.mean_reversion_to_ma import MeanReversionToMaPattern

# --- batch 4: cup-and-handle + rounded + gaps ---
from app.core.patterns.chart.cup_and_handle import CupAndHandlePattern
from app.core.patterns.chart.inverse_cup_and_handle import InverseCupAndHandlePattern
from app.core.patterns.chart.rounding_bottom import RoundingBottomPattern
from app.core.patterns.chart.saucer_bottom import SaucerBottomPattern
from app.core.patterns.chart.saucer_top import SaucerTopPattern
from app.core.patterns.chart.common_gap import CommonGapPattern
from app.core.patterns.chart.breakaway_gap_up import BreakawayGapUpPattern
from app.core.patterns.chart.breakaway_gap_down import BreakawayGapDownPattern
from app.core.patterns.chart.runaway_gap_up import RunawayGapUpPattern
from app.core.patterns.chart.runaway_gap_down import RunawayGapDownPattern
from app.core.patterns.chart.exhaustion_gap_up import ExhaustionGapUpPattern
from app.core.patterns.chart.exhaustion_gap_down import ExhaustionGapDownPattern
from app.core.patterns.chart.double_gap_continuation_up import (
    DoubleGapContinuationUpPattern,
)
from app.core.patterns.chart.double_gap_continuation_down import (
    DoubleGapContinuationDownPattern,
)
from app.core.patterns.chart.gap_window import GapWindowPattern

# --- batch 5: complex multi-bar + multi-TF convergence ---
from app.core.patterns.chart.two_bar_reversal_top import TwoBarReversalTopPattern
from app.core.patterns.chart.two_bar_reversal_bottom import (
    TwoBarReversalBottomPattern,
)
from app.core.patterns.chart.three_drives_top import ThreeDrivesTopPattern
from app.core.patterns.chart.three_drives_bottom import ThreeDrivesBottomPattern
from app.core.patterns.chart.harmonic_gartley import HarmonicGartleyPattern
from app.core.patterns.chart.harmonic_butterfly import HarmonicButterflyPattern
from app.core.patterns.chart.harmonic_bat import HarmonicBatPattern
from app.core.patterns.chart.harmonic_crab import HarmonicCrabPattern
from app.core.patterns.chart.abcd_pattern import AbcdPatternPattern
from app.core.patterns.chart.multi_tf_confluence_long import (
    MultiTfConfluenceLongPattern,
)
from app.core.patterns.chart.multi_tf_confluence_short import (
    MultiTfConfluenceShortPattern,
)
from app.core.patterns.chart.volume_climax_top import VolumeClimaxTopPattern
from app.core.patterns.chart.volume_climax_bottom import VolumeClimaxBottomPattern
from app.core.patterns.chart.opening_range_breakout import (
    OpeningRangeBreakoutPattern,
)
from app.core.patterns.chart.end_of_day_reversal import EndOfDayReversalPattern
from app.core.patterns.chart.weekly_pivot_break import WeeklyPivotBreakPattern

CHART_PATTERNS: list[Pattern] = [
    # batch 1
    DoubleTopPattern(),
    DoubleBottomPattern(),
    TripleTopPattern(),
    TripleBottomPattern(),
    HeadAndShouldersPattern(),
    InverseHeadAndShouldersPattern(),
    VTopPattern(),
    VBottomPattern(),
    IslandReversalTopPattern(),
    IslandReversalBottomPattern(),
    KeyReversalHighPattern(),
    KeyReversalLowPattern(),
    BumpAndRunTopPattern(),
    BumpAndRunBottomPattern(),
    RoundingTopPattern(),
    # batch 2
    AscendingTrianglePattern(),
    DescendingTrianglePattern(),
    SymmetricalTrianglePattern(),
    BullishFlagPattern(),
    BearishFlagPattern(),
    BullishPennantPattern(),
    BearishPennantPattern(),
    RectangleContinuationPattern(),
    ExpandingTrianglePattern(),
    BroadeningTopPattern(),
    BroadeningBottomPattern(),
    DiamondTopPattern(),
    DiamondBottomPattern(),
    ChannelUpPattern(),
    ChannelDownPattern(),
    # batch 3
    RisingWedgePattern(),
    FallingWedgePattern(),
    EndingDiagonalTopPattern(),
    EndingDiagonalBottomPattern(),
    RegressionChannelUpPattern(),
    RegressionChannelDownPattern(),
    ParallelChannelPattern(),
    AndrewsPitchforkPattern(),
    FibonacciRetracement50Pattern(),
    FibonacciRetracement618Pattern(),
    GannFanSignalPattern(),
    MidTermConsolidationPattern(),
    BreakoutPullbackLongPattern(),
    BreakoutPullbackShortPattern(),
    MeanReversionToMaPattern(),
    # batch 4
    CupAndHandlePattern(),
    InverseCupAndHandlePattern(),
    RoundingBottomPattern(),
    SaucerBottomPattern(),
    SaucerTopPattern(),
    CommonGapPattern(),
    BreakawayGapUpPattern(),
    BreakawayGapDownPattern(),
    RunawayGapUpPattern(),
    RunawayGapDownPattern(),
    ExhaustionGapUpPattern(),
    ExhaustionGapDownPattern(),
    DoubleGapContinuationUpPattern(),
    DoubleGapContinuationDownPattern(),
    GapWindowPattern(),
    # batch 5
    TwoBarReversalTopPattern(),
    TwoBarReversalBottomPattern(),
    ThreeDrivesTopPattern(),
    ThreeDrivesBottomPattern(),
    HarmonicGartleyPattern(),
    HarmonicButterflyPattern(),
    HarmonicBatPattern(),
    HarmonicCrabPattern(),
    AbcdPatternPattern(),
    MultiTfConfluenceLongPattern(),
    MultiTfConfluenceShortPattern(),
    VolumeClimaxTopPattern(),
    VolumeClimaxBottomPattern(),
    OpeningRangeBreakoutPattern(),
    EndOfDayReversalPattern(),
    WeeklyPivotBreakPattern(),
]
