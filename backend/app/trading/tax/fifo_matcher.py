"""SP-8 Phase H — FIFO cost-basis matcher.

Indian tax law (Section 115BBH + applicable to VDAs) requires First-In
First-Out cost basis: when you sell a unit, the basis is the OLDEST
remaining buy of the same asset on the same exchange. Per spec §8.2.

The matcher maintains a per-(user_id, symbol, exchange) FIFO queue of
remaining buys. Each entry is a (timestamp, qty_remaining, cost_per_unit_inr)
tuple. On a sell, we dequeue from the front, partially consuming the
oldest entry until either the entry is exhausted (popped) or the sell
quantity is fully matched.

Pure data structure — no DB. The caller persists the queue (or
recomputes from live_trades on each event) and the matched entries
for the audit row.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


_QueueKey = tuple[int, str, str]  # (user_id, symbol, exchange)


@dataclass(frozen=True)
class CostLot:
    """A buy lot waiting to be matched against a future sell."""

    purchased_at: datetime
    qty_remaining: float
    cost_per_unit_inr: float
    source_trade_id: int  # the live_trades.id that opened this lot


@dataclass(frozen=True)
class FIFOMatch:
    """A single buy-lot fragment consumed by a sell.

    A single sell may match against multiple lots (partial match
    against each); the caller records one or more of these per sell.
    """

    sell_qty: float                  # how much of the sell this fragment covers
    cost_basis_per_unit_inr: float
    cost_basis_total_inr: float       # sell_qty * cost_per_unit_inr
    matched_against_trade_id: int     # source_trade_id of the lot


@dataclass
class FIFOQueue:
    """Per-(user, symbol, exchange) FIFO queue of remaining buy lots.

    Use ``record_buy`` after a long-open trade and ``match_sell`` when
    a long position closes. For shorts, the analog is reversed —
    ``record_short_open`` then ``match_short_close`` — but the queue
    structure is identical.
    """

    lots: dict[_QueueKey, list[CostLot]] = field(
        default_factory=lambda: defaultdict(list),
    )

    def record_buy(
        self, *,
        user_id: int, symbol: str, exchange: str,
        qty: float, cost_per_unit_inr: float,
        purchased_at: datetime, source_trade_id: int,
    ) -> None:
        """Append a new buy lot to the back of the queue."""
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        self.lots[(user_id, symbol, exchange)].append(CostLot(
            purchased_at=purchased_at,
            qty_remaining=qty,
            cost_per_unit_inr=cost_per_unit_inr,
            source_trade_id=source_trade_id,
        ))

    def match_sell(
        self, *,
        user_id: int, symbol: str, exchange: str, sell_qty: float,
    ) -> list[FIFOMatch]:
        """Consume sell_qty from the front of the queue.

        Returns the list of (lot-fragment, cost-basis) matches in the
        order they were consumed. Mutates the queue in place: fully-
        consumed lots are popped, partials are decremented.

        Raises:
            ValueError if the queue lacks enough buy quantity to match
            the sell. Spec §8.2 expects this to never happen in a
            well-formed pipeline (you can't sell what you didn't buy);
            raising loudly catches bookkeeping bugs.
        """
        if sell_qty <= 0:
            raise ValueError(f"sell_qty must be positive, got {sell_qty}")
        key = (user_id, symbol, exchange)
        queue = self.lots.get(key, [])

        total_remaining = sum(lot.qty_remaining for lot in queue)
        if total_remaining < sell_qty - 1e-9:
            raise ValueError(
                f"queue for {key} has only {total_remaining} units; "
                f"cannot match sell of {sell_qty}"
            )

        matches: list[FIFOMatch] = []
        outstanding = sell_qty
        while outstanding > 1e-12 and queue:
            head = queue[0]
            take = min(head.qty_remaining, outstanding)
            matches.append(FIFOMatch(
                sell_qty=take,
                cost_basis_per_unit_inr=head.cost_per_unit_inr,
                cost_basis_total_inr=take * head.cost_per_unit_inr,
                matched_against_trade_id=head.source_trade_id,
            ))
            outstanding -= take
            new_remaining = head.qty_remaining - take
            if new_remaining <= 1e-9:
                queue.pop(0)
            else:
                queue[0] = CostLot(
                    purchased_at=head.purchased_at,
                    qty_remaining=new_remaining,
                    cost_per_unit_inr=head.cost_per_unit_inr,
                    source_trade_id=head.source_trade_id,
                )
        return matches

    def total_qty(
        self, *, user_id: int, symbol: str, exchange: str,
    ) -> float:
        """Sum of qty_remaining across all lots in the queue (for sanity checks)."""
        return sum(
            lot.qty_remaining
            for lot in self.lots.get((user_id, symbol, exchange), [])
        )

    def weighted_avg_cost_inr(
        self, *, user_id: int, symbol: str, exchange: str,
    ) -> float:
        """Weighted-average cost across the queue (display only — not used
        for tax: the matcher is FIFO, this is a UI helper)."""
        queue = self.lots.get((user_id, symbol, exchange), [])
        total_qty = sum(lot.qty_remaining for lot in queue)
        if total_qty <= 0:
            return 0.0
        total_cost = sum(
            lot.qty_remaining * lot.cost_per_unit_inr for lot in queue
        )
        return total_cost / total_qty


# ---- Direction semantics ------------------------------------------------


def direction_for_open(direction: Literal["LONG", "SHORT"]) -> str:
    """LONG opens are 'buys' for FIFO purposes; SHORT opens are 'sells'."""
    return "buy" if direction == "LONG" else "sell"


def direction_for_close(direction: Literal["LONG", "SHORT"]) -> str:
    """Closing a LONG is a 'sell'; closing a SHORT is a 'buy-back'."""
    return "sell" if direction == "LONG" else "buy"


__all__ = [
    "CostLot",
    "FIFOMatch",
    "FIFOQueue",
    "direction_for_close",
    "direction_for_open",
]
