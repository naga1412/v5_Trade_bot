"""Chart pattern registry — extended by individual pattern modules at import.

Phase D populates this. Until then, the list is empty.
"""
from app.core.patterns.base import Pattern

CHART_PATTERNS: list[Pattern] = []
