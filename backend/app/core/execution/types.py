from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.core.scoring.types import Direction


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIMEOUT = "TIMEOUT"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class Signal:
    symbol: str
    timeframe: str
    ts: datetime
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float          # fraction of portfolio (e.g. 0.01 = 1%)
    confidence: float             # [0, 1]
    reasoning: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction is Direction.NEUTRAL:
            raise ValueError("Signal cannot have NEUTRAL direction")
        if self.position_size <= 0:
            raise ValueError("position_size must be positive")

    @property
    def risk_reward(self) -> float:
        if self.direction is Direction.LONG:
            risk = self.entry_price - self.stop_loss
            reward = self.take_profit - self.entry_price
        else:
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.take_profit
        return reward / risk if risk > 0 else 0.0


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    opened_at: datetime
    closed_at: datetime
    bars_held: int
    exit_reason: ExitReason
    reasoning: dict[str, Any] = field(default_factory=dict)

    @property
    def pnl_pct(self) -> float:
        if self.direction is Direction.LONG:
            return (self.exit_price - self.entry_price) / self.entry_price * 100.0
        return (self.entry_price - self.exit_price) / self.entry_price * 100.0
