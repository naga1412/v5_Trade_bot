"""Stub — implemented in SP-9 Phase D1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class FngResult:
    value: int
    label: Literal["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
    timestamp: datetime


async def get_fear_greed_index() -> FngResult:
    raise NotImplementedError("SP-9 Phase D1")
