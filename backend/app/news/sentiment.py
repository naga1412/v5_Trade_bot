"""Stub — implemented in SP-9 Phase C1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SentimentResult:
    score: float
    label: Literal["positive", "negative", "neutral"]
    confidence: float


def classify_batch(titles: list[str], batch_size: int = 16) -> list[SentimentResult]:
    raise NotImplementedError("SP-9 Phase C1")
