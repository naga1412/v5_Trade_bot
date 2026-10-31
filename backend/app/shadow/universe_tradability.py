"""Which universe members are NOT TRADABLE right now. One implementation.

THE QUESTION. A symbol whose futures status is not TRADING cannot be
entered *or exited* by anyone. That is unexitable in the strongest sense --
not "illiquid" but "no counterparty exists" -- and the fleet's liquidity
hysteresis cannot detect it, because hysteresis measures depth and spread
on an order book that is not there.

Entry is already safe: ``refresh_live_fleet_universe`` builds ``candidates``
from ``tradable_futures``, so a non-TRADING symbol cannot be admitted. The
exposure is RETENTION -- the open-position override re-adds every symbol in
``get_open_position_symbols()`` with no tradability test, and a position on
a non-TRADING symbol can never close, so the override re-adds it forever.

WHY THIS MODULE EXISTS RATHER THAN THE SCRIPT ALONE. The audit shipped on
2026-10-29 as ``scripts/universe_tradable_audit.py``, on-demand, with
nothing scheduling it -- and the re-open trigger for the universe
constraint was "when the audit reports non-zero", which nothing could ever
report. That is the sixth instance in this repo of instrumentation with no
retrieval path (FU-47, the entry-timing recon, #447's streaks, #601
log-only, #610's homeless counter, and this). #612's registry-driven
silence detector exists for exactly that, so the audit now has a producer
and a registry entry, and the script and the producer share the ONE
implementation below.

:func:`audit_tradability` is PURE -- sets in, dataclass out, no I/O. That
is what makes it exhaustively testable over the status combinations, and
what makes "the script and the worker agree" a structural fact rather than
a thing to re-verify.

TRADABILITY IS A TWO-MARKET QUESTION, and the first version of the script
got this wrong. It tested ``s not in tradable_futures`` alone, which
conflates two unrelated things:

  1. listed as a USDT perp but HALTED or SETTLING -- genuinely untradable,
     the case this exists for; and
  2. NOT A USDT PERP AT ALL -- a spot-only symbol, perfectly tradable,
     just not on the market the fleet universe is built from.

Shadow open positions include spot-only symbols, so applying a
futures-membership test to that population reported ``PEPEUSDT`` (spot; the
perp is ``1000PEPEUSDT``) and ``SOXLBUSDT`` as untradable when both are
fine. Same shape as the standing pooled-rate rule: name the population a
predicate was defined over AND the one it is applied to, and if they
differ, that IS the defect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class TradabilityAudit:
    """The four populations, kept SEPARATE because they differ in consequence.

    Collapsing them into one count is the ``not_evaluated`` mistake: a
    caller that cannot format two states differently will report them
    identically, and these four need different human responses.
    """

    # FINDINGS ----------------------------------------------------------
    # In the universe and no market will take an order. The condition the
    # module exists for.
    not_tradable: tuple[str, ...]
    # Of those, the ones an open position is holding in -- unexitable.
    held_open: tuple[str, ...]
    # Open position, not tradable, not yet a member: these would be
    # ADMITTED by the override's no-prior-entry branch.
    open_not_member: tuple[str, ...]

    # NOT A FINDING -----------------------------------------------------
    # Open position on a spot-TRADING symbol that is not a USDT perp.
    # Tradable; the universe predicate simply does not describe it.
    open_spot_only: tuple[str, ...]

    # CONTEXT, so a reader can tell a real empty result from an empty input
    members: int
    open_positions: int
    tradable_futures: int
    tradable_spot: int

    @property
    def has_finding(self) -> bool:
        """True if anything needs a human. ``open_spot_only`` is excluded.

        Deliberately not ``bool(not_tradable)``: ``open_not_member`` is a
        finding on its own -- the override would admit such a symbol on the
        next refresh, so it is a pending admission rather than a current
        member.
        """
        return bool(self.not_tradable or self.open_not_member)

    @property
    def inputs_look_empty(self) -> bool:
        """An empty result is only meaningful if the inputs were not empty.

        A zero finding over a zero-symbol snapshot or an unloaded listing
        set is the vacuous pass, not a clean bill of health, and the caller
        must be able to tell them apart.
        """
        return (
            self.members == 0
            or self.tradable_futures == 0
            or self.tradable_spot == 0
        )


def describe_market_status(
    symbol: str,
    *,
    tradable_futures: frozenset[str] | set[str],
    tradable_spot: frozenset[str] | set[str],
    routable: frozenset[str] | set[str],
) -> str:
    """An explicit per-market verdict -- never an inference from absence.

    Each market is named with its own answer, so a reader never has to
    deduce "not in the futures set" into any particular meaning. That
    deduction is what produced the two false positives above.
    """
    return " ".join((
        "futures=TRADING" if symbol in tradable_futures
        else "futures=not-TRADING/absent",
        "spot=TRADING" if symbol in tradable_spot
        else "spot=not-TRADING/absent",
        "has-history" if symbol in routable else "no-history",
    ))


def audit_tradability(
    *,
    members: frozenset[str] | set[str],
    open_positions: frozenset[str] | set[str],
    tradable_futures: frozenset[str] | set[str],
    tradable_spot: frozenset[str] | set[str],
) -> TradabilityAudit:
    """Classify the universe against both markets. Pure: sets in, result out."""

    def untradable(sym: str) -> bool:
        """No market will take an order for this symbol, either side."""
        return sym not in tradable_futures and sym not in tradable_spot

    not_tradable = tuple(sorted(s for s in members if untradable(s)))
    return TradabilityAudit(
        not_tradable=not_tradable,
        held_open=tuple(s for s in not_tradable if s in open_positions),
        open_not_member=tuple(sorted(
            s for s in open_positions
            if untradable(s) and s not in members
        )),
        open_spot_only=tuple(sorted(
            s for s in open_positions
            if s not in tradable_futures and s in tradable_spot
        )),
        members=len(members),
        open_positions=len(open_positions),
        tradable_futures=len(tradable_futures),
        tradable_spot=len(tradable_spot),
    )


async def audit_from_cached_listings(
    session: "AsyncSession",
) -> TradabilityAudit | None:
    """Run the audit using the CACHED listings. No network call.

    Returns ``None`` when the listing cache has never been populated,
    which the caller must report rather than treat as a pass -- that is
    rule 2, and ``inputs_look_empty`` covers the other half of it.

    WHY THE CACHE AND NOT A FRESH FETCH. The producer is the 5-minute
    ``check_events_flush`` cycle, and two ``exchangeInfo`` calls every five
    minutes is 576 requests a day to re-read a property that changes on the
    order of weeks. The cache is refreshed every 24h by
    ``app.workers.futures_only_refresh``, so a status change is detected
    within ~24h of Binance publishing it, and within 5 minutes of the cache
    learning it.

    THAT LATENCY IS STATED BECAUSE IT IS INHERITED, NOT CHOSEN: the same
    cache is what ``app.data.bar_fetch`` routes every bar fetch on, so the
    system already depends on this staleness bound for something more
    load-bearing than an audit. An audit with a tighter bound than the
    routing it audits would be measuring a world the rest of the process
    does not live in.
    """
    from app.shadow.cohort_cache import (
        get_spot_listed_cache,
        get_tradable_futures_cache,
    )
    from app.shadow.live_fleet_universe import (
        get_open_position_symbols,
        load_live_fleet_universe,
    )

    # BOTH sets are read as themselves. An earlier draft of this
    # function reconstructed the futures side as
    # `futures_only | spot`, because the cache did not carry
    # `tradable_futures`. That union is the right answer to
    # "tradable on at least one market" and the WRONG answer to
    # either market's own question, so it silently emptied the
    # `open_spot_only` category -- a reader would have seen a field
    # that is always zero and had no way to tell that from a clean
    # result. Adding the set to the cache cost nothing (it comes out
    # of the same two exchangeInfo calls) and removed the inference.
    futures = get_tradable_futures_cache()
    spot = get_spot_listed_cache()
    if futures is None or spot is None:
        return None

    snapshot = await load_live_fleet_universe(session)
    open_syms = await get_open_position_symbols(session)

    return audit_tradability(
        members={e.symbol for e in snapshot},
        open_positions=set(open_syms),
        tradable_futures=futures,
        tradable_spot=spot,
    )


__all__ = [
    "TradabilityAudit",
    "audit_from_cached_listings",
    "audit_tradability",
    "describe_market_status",
]
