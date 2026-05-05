"""Pattern registry — populated by importing the candle and chart subpackages.

Each subpackage's __init__.py extends `_REGISTRY` with its own detectors.
The exposed `ALL_PATTERNS` is the concatenation, in candle-then-chart order.
"""
from app.core.patterns.base import Pattern, PatternFire  # noqa: F401  re-export
from app.core.patterns.candle import CANDLE_PATTERNS
from app.core.patterns.chart import CHART_PATTERNS

ALL_PATTERNS: list[Pattern] = [*CANDLE_PATTERNS, *CHART_PATTERNS]
