from dataclasses import dataclass
from datetime import datetime

from app.core.execution.types import ExitReason, Signal, Trade
from app.core.scoring.types import Direction


@dataclass
class _OpenPosition:
    signal: Signal
    bars_held: int = 0


class PaperEngine:
    """In-memory paper trading engine.

    SP-0 keeps state in memory (single process, single asset). SP-4 will
    persist positions and integrate with the RL brain for reward signals.
    Pessimistic exit assumption when both SL and TP are touched in the same
    bar: assumes SL hit first (worst case for the trader).
    """

    def __init__(self) -> None:
        self._positions: dict[str, _OpenPosition] = {}
        self._closed: list[Trade] = []

    def on_signal(self, signal: Signal) -> bool:
        if signal.symbol in self._positions:
            return False
        if signal.direction is Direction.NEUTRAL:
            return False
        self._positions[signal.symbol] = _OpenPosition(signal=signal)
        return True

    def open_position(self, symbol: str) -> Signal | None:
        pos = self._positions.get(symbol)
        return pos.signal if pos else None

    def on_bar(
        self, symbol: str, ts: datetime, *, high: float, low: float, close: float
    ) -> Trade | None:
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        pos.bars_held += 1
        sig = pos.signal

        sl_hit = (sig.direction is Direction.LONG and low <= sig.stop_loss) or (
            sig.direction is Direction.SHORT and high >= sig.stop_loss
        )
        tp_hit = (sig.direction is Direction.LONG and high >= sig.take_profit) or (
            sig.direction is Direction.SHORT and low <= sig.take_profit
        )

        if not sl_hit and not tp_hit:
            return None

        # Pessimistic: SL first if both
        if sl_hit:
            exit_price = sig.stop_loss
            reason = ExitReason.STOP_LOSS
        else:
            exit_price = sig.take_profit
            reason = ExitReason.TAKE_PROFIT

        trade = Trade(
            symbol=sig.symbol,
            direction=sig.direction,
            entry_price=sig.entry_price,
            exit_price=exit_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            position_size=sig.position_size,
            opened_at=sig.ts,
            closed_at=ts,
            bars_held=pos.bars_held,
            exit_reason=reason,
            reasoning=sig.reasoning,
        )
        del self._positions[symbol]
        self._closed.append(trade)
        return trade

    @property
    def closed_trades(self) -> list[Trade]:
        return list(self._closed)
