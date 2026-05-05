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
