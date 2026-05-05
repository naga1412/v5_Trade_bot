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
