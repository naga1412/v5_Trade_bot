"""Candle pattern registry — extended by individual pattern modules at import.

Phase C populates this. Until then, the list is empty.
"""
from app.core.patterns.base import Pattern

CANDLE_PATTERNS: list[Pattern] = []
from app.core.patterns.candle.hammer import HammerPattern
CANDLE_PATTERNS.append(HammerPattern())
from app.core.patterns.candle.inverted_hammer import InvertedHammerPattern
CANDLE_PATTERNS.append(InvertedHammerPattern())
