"""Phase 4 liquidity-floor selector -- addendum to
docs/superpowers/specs/2026-08-14-phase4-futures-signal-coverage-design.md
(2026-08-17). Replaces top-N-by-volume with liquidity-floor pass/fail
across the full market, with hysteresis. See the decision record at
docs/superpowers/decisions/2026-08-15-liquidity-floor-selector-supersedes-top20.md.

This is the single shared selector both `ws_keepalive_task` (Task 5b,
spot-backed cohorts) and `futures_poll_task` (Task 8, futures-only
cohort) read from -- "one mechanism closes both coverage gaps."

Hysteresis (addendum section (a)):
  - 5 samples per candidate, ~10s apart (order-book spread flickers
    within seconds on thin-tick symbols -- a single point-in-time read
    would inherit that noise at the selection layer).
  - Entry: not-currently-a-member needs >=3 of 5 passes.
  - Exit: currently-a-member needs a UNANIMOUS 5 of 5 fails (i.e. must
    fail strictly more than EXIT_MAX_PASSES=0 passes to be retained).
  - Order of operations matters: the previous snapshot ("is this symbol
    currently a member?") is loaded BEFORE this tick's samples are
    taken, because which threshold applies (3-of-5 vs unanimous-fail)
    depends on that prior state, not on anything this tick measures.
  - Minimum 24h dwell is satisfied structurally by the daily refresh
    cadence itself (see note above Step 8 in the plan doc) -- no
    separate dwell-timer state is tracked here.
  - Open-position override: a symbol with an open live_trades or
    shadow_open_positions row is never removed from the fleet while
    that position is open, regardless of liquidity-check outcome. This
    guards against REMOVAL of an existing member (addendum (a) point 4:
    "never removed... until the position closes") -- it is not an
    entry guarantee for a brand-new candidate that fails the 3-of-5
    entry bar. Task 5d (ratified 2026-08-19, see "Open-position
    retention at the source" below) made this a real guarantee for
    open positions specifically, independent of prior snapshot
    membership -- the override queries live open positions directly
    now, not just `prior`.

Cold-start seeding (addendum section (c) / plan doc Task 5 note 2): the
very first time this table is ever refreshed (no prior snapshot exists
at all), a candidate that also appears in today's legacy top-20-by-
volume fleet (`asset_universe` via `load_current_universe`, filtered to
`KEEPALIVE_TOP_N`) is tagged `established_top20` instead of
`liquidity_added_spot` -- a lineage marker for "was already covered
before the cutover", not a live-recomputed rank. This branch is gated
on `if not prior:` so it structurally cannot re-fire on any later
refresh (the first successful refresh always persists at least one row,
making every subsequent `prior` non-empty), and a symbol already a
member on a later refresh always takes its cohort from
`prior[sym].cohort` (sticky, never recomputed) rather than from this
branch.

Cold-start single-sample fast path (Task 5e, ratified 2026-08-19,
correcting Task 5c the same day, before the first live sweep even
completed -- docs/superpowers/decisions/2026-08-19-live-fleet-universe-
never-scheduled-incident.md, ruling 3 + its "Implementation note"):
on that SAME very-first-ever refresh (no prior snapshot at all), each
candidate takes exactly ONE `check_liquidity` sample -- not the normal
5-sample, ~10s-apart microsampling loop -- and admission is that single
sample's pass/fail, full stop. Task 5c's original fix (`
COLD_START_ENTRY_MIN_PASSES`, now removed) only lowered the PASS-COUNT
THRESHOLD from 3-of-5 to 1-of-5 while leaving the 5-sample sleep loop
itself unconditional, so a cold-start sweep still took the full
~50-60 minutes across the ~70+ qvol-qualifying candidates -- directly
contradicting the operator's explicit ruling ("admit everything passing
the floor on that SINGLE sample... a fresh environment must not sit
dark"). Confirmed on staging: the first sweep was still running 15+
minutes in with zero worker heartbeats and both fleet supervisors still
at `children: 0`. This single-sample path is the actual fix. Gated on
the identical `if not prior:` condition as the legacy-cohort-tagging
branch above, for the identical structural reason: this table is never
empty again after its first successful refresh (step 5 always persists
at least one row), so this branch cannot re-fire on any later call.
Every subsequent refresh (`prior` non-empty) is completely unchanged:
full 5-sample loop, unchanged 3-of-5 entry / unanimous-fail exit
thresholds.

Open-position retention at the source (Task 5d, ratified 2026-08-19 --
same decision record, ruling 4): the open-position override above used
to iterate `prior.items()` only -- on the table's very first-ever
refresh (`prior` empty: fresh deployment, table wiped, or a
fleet-supervisor cold-start restart before any refresh has ever run),
that loop protects nothing, regardless of what positions are genuinely
open at that moment. `get_open_position_symbols` fixes this by querying
`live_trades`/`shadow_open_positions` directly every refresh instead of
only consulting the prior snapshot's membership -- closing the gap for
cold start, mid-window supervisor restarts, AND steady-state operation
all at once. `ws_keepalive_task` and `futures_poll_task` inherit the
fix automatically through their existing `live_fleet_universe` reads;
no changes needed in `keepalive.py` or `futures_poll.py`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.futures_liquidity import QVOL_FLOOR_USDT, check_liquidity
from app.data.ratelimit import RateLimitedClient

log = logging.getLogger(__name__)

_FUTURES_BASE = "https://fapi.binance.com"
_SPOT_BASE = "https://api.binance.com"

N_SAMPLES: int = 5
SAMPLE_GAP_SECONDS: float = 10.0
ENTRY_MIN_PASSES: int = 3   # of N_SAMPLES
EXIT_MAX_PASSES: int = 0    # exit requires unanimous failure -- 0 passes of 5
# COLD_START_ENTRY_MIN_PASSES removed (Task 5e, ratified 2026-08-19): it was
# a redundant relaxed-threshold constant from Task 5c's incomplete fix -- see
# module docstring's "Cold-start single-sample fast path". A single sample's
# "at least 1 of 1 passes" is just "did it pass", so no threshold constant
# is needed for the cold-start branch at all.

Cohort = Literal["established_top20", "liquidity_added_spot", "futures_poll"]


@dataclass(frozen=True)
class LiveFleetEntry:
    symbol: str
    cohort: Cohort
    qvol_24h: float
    spread_bps: float
    depth_0_5pct_usdt: float


async def has_open_position(session: AsyncSession, symbol_pair: str) -> bool:
    """True if symbol_pair has an open live_trades OR shadow_open_positions
    row. Checked before ANY exit -- hard override, not best-effort.

    The two tables store the symbol in two DIFFERENT formats, which is
    NOT a normalization nicety -- querying both with the same literal
    string silently never matches one side:

      - live_trades.symbol is the canonical BASE/QUOTE slash form (e.g.
        "BTC/USDT") -- see
        app.trading.execution.symbol_position_gate.get_open_position_trade_id's
        docstring ("matches how live_trades.symbol is written").
      - shadow_open_positions.symbol is Binance no-slash form (e.g.
        "BTCUSDT") -- see app.api.routes.bot_status's comment ("Symbols
        stored in shadow_open_positions are already in Binance no-slash
        form").

    `symbol_pair` (matching the naming/format `to_pair()` produces and
    the format `app.ws.keepalive` passes around) is expected in slash
    form; it is normalized per-table below.
    """
    no_slash = symbol_pair.replace("/", "")
    row = (await session.execute(
        sa.text(
            "SELECT 1 FROM live_trades WHERE status = 'open' AND symbol = :sp "
            "UNION ALL "
            "SELECT 1 FROM shadow_open_positions WHERE symbol = :ns "
            "LIMIT 1"
        ),
        {"sp": symbol_pair, "ns": no_slash},
    )).first()
    return row is not None


async def get_open_position_symbols(session: AsyncSession) -> set[str]:
    """Every symbol with a currently-open live_trades or shadow_open_positions
    row, normalized to no-slash form (this table's own symbol convention).
    Companion to has_open_position (which checks one symbol) -- this is the
    bulk form the open-position override needs, since it must protect every
    open position each refresh, not test one candidate at a time.

    Task 5d (ratified 2026-08-19): this is the query the open-position
    override now runs directly, instead of only iterating the prior
    snapshot's own membership -- see refresh_live_fleet_universe's override
    step below for why that mattered (prior is empty on the very first
    refresh, so a prior-only loop protects nothing at cold start)."""
    rows = await session.execute(sa.text(
        "SELECT symbol FROM live_trades WHERE status = 'open' "
        "UNION "
        "SELECT symbol FROM shadow_open_positions"
    ))
    out: set[str] = set()
    for r in rows:
        out.add(r.symbol.replace("/", ""))
    return out


async def load_live_fleet_universe(
    session: AsyncSession, *, cohort: str | None = None,
) -> list[LiveFleetEntry]:
    """Read the latest snapshot, optionally filtered to one cohort."""
    where_cohort = "AND cohort = :cohort " if cohort else ""
    params: dict = {"cohort": cohort} if cohort else {}
    rows = await session.execute(
        sa.text(
            "SELECT symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt "
            "FROM live_fleet_universe "
            "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM live_fleet_universe) "
            + where_cohort
        ),
        params,
    )
    return [
        LiveFleetEntry(
            symbol=r.symbol, cohort=r.cohort, qvol_24h=r.qvol_24h,
            spread_bps=r.spread_bps, depth_0_5pct_usdt=r.depth_0_5pct_usdt,
        )
        for r in rows
    ]


async def refresh_live_fleet_universe(
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    rate_client: RateLimitedClient,
) -> list[LiveFleetEntry]:
    """The daily job. See module docstring for the design this implements.

    Order of operations (load-bearing for the hysteresis rule):
      1. Fetch the full market (futures + spot symbol sets, bulk 24h
         tickers) and cheap-prefilter candidates on qvol alone.
      2. Load the PRIOR snapshot (and, only if there is none at all,
         the cold-start legacy-fleet seed) -- this happens BEFORE any
         of this tick's check_liquidity samples are taken, because
         "is this symbol currently a member" determines which
         hysteresis threshold (3-of-5 entry vs unanimous-fail exit)
         applies to THIS tick's samples.
      3. Sample check_liquidity per candidate and apply the
         direction-dependent threshold from step 2's prior state -- N_SAMPLES
         times (~10s apart) on every refresh EXCEPT the very first-ever one,
         where it's a single sample (Task 5e cold-start fast path, see
         module docstring).
      4. Re-add anything with a currently-open live position that isn't
         already in results (hard override, Task 5d): prior liquidity
         numbers carried forward unchanged if a prior entry exists,
         otherwise one fresh check_liquidity sample tagged
         established_top20 as the safe default (see
         get_open_position_symbols / module docstring for why this
         queries live positions directly rather than only `prior`).
      5. Persist the new snapshot and return it.
    """
    futures_resp = await http.get(f"{_FUTURES_BASE}/fapi/v1/exchangeInfo")
    futures_resp.raise_for_status()
    fut_symbols = {
        s["symbol"] for s in futures_resp.json()["symbols"]
        if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"
        and s.get("status") == "TRADING"
    }

    spot_resp = await http.get(f"{_SPOT_BASE}/api/v3/exchangeInfo")
    spot_resp.raise_for_status()
    spot_symbols = {
        s["symbol"] for s in spot_resp.json()["symbols"]
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
    }
    futures_only = fut_symbols - spot_symbols

    ticker_resp = await http.get(f"{_FUTURES_BASE}/fapi/v1/ticker/24hr")
    ticker_resp.raise_for_status()
    qvol_by_symbol = {t["symbol"]: float(t["quoteVolume"]) for t in ticker_resp.json()}

    # Cheap pre-filter -- skip the 5x-sampled depth check entirely for
    # anything that already fails on volume alone (the large majority).
    candidates = sorted(s for s in fut_symbols if qvol_by_symbol.get(s, 0.0) >= QVOL_FLOOR_USDT)

    async with session_factory() as session:
        prior = {e.symbol: e for e in await load_live_fleet_universe(session)}

        # Cold-start seed: no prior snapshot at all -- consult today's
        # legacy top-20-by-volume fleet so pre-cutover coverage gets
        # tagged established_top20, not liquidity_added_spot. Gated on
        # `not prior` so this can only ever fire on the very first
        # successful refresh (every refresh from here on persists at
        # least one row, per step 5 below, so `prior` is non-empty on
        # every subsequent call).
        legacy_top20: set[str] = set()
        if not prior:
            from app.shadow.universe import load_current_universe
            from app.ws.keepalive import KEEPALIVE_TOP_N, to_pair
            try:
                legacy_entries = await load_current_universe(session)
                legacy_top20 = {
                    to_pair(e.symbol).replace("/", "")
                    for e in legacy_entries[:KEEPALIVE_TOP_N]
                }
            except Exception as e:  # noqa: BLE001 -- cold-start seed is best-effort
                log.warning("live_fleet_universe: legacy top-20 seed failed: %s", e)

    results: dict[str, LiveFleetEntry] = {}
    for sym in candidates:
        currently_in = sym in prior
        if not prior:
            # Task 5e (ratified 2026-08-19, correcting Task 5c the same
            # day): cold-start fast path -- one sample, not five. The
            # normal 5-sample microsampling exists to smooth SECONDS-scale
            # order-book noise for a threshold decision; at cold start
            # there is no hysteresis history to protect, and the
            # operator's ruling was explicit that a single sample decides
            # admission here, not a relaxed-threshold version of the full
            # loop (which was Task 5c's mistake -- it changed the
            # threshold but not the sample count, so cold start still
            # took ~50-60 minutes for the full market). `currently_in` is
            # always False here (an empty `prior` cannot contain `sym`),
            # so this branch only ever evaluates entry, never exit. Every
            # later refresh (prior non-empty) is unchanged.
            try:
                last_check = await check_liquidity(sym, rate_client)
            except Exception as e:  # noqa: BLE001
                log.warning("live_fleet_universe: check_liquidity failed for %s: %s", sym, e)
                continue
            keep = last_check.passed
        else:
            pass_count = 0
            last_check = None
            for i in range(N_SAMPLES):
                try:
                    last_check = await check_liquidity(sym, rate_client)
                    if last_check.passed:
                        pass_count += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("live_fleet_universe: check_liquidity failed for %s: %s", sym, e)
                if i < N_SAMPLES - 1:
                    await asyncio.sleep(SAMPLE_GAP_SECONDS)
            if last_check is None:
                continue
            if currently_in:
                keep = pass_count > EXIT_MAX_PASSES  # anything but unanimous failure
            else:
                keep = pass_count >= ENTRY_MIN_PASSES

        if not keep:
            continue

        cohort: Cohort
        if currently_in:
            cohort = prior[sym].cohort  # sticky -- inherited, never recomputed
        elif sym in legacy_top20:
            cohort = "established_top20"
        elif sym in futures_only:
            cohort = "futures_poll"
        else:
            cohort = "liquidity_added_spot"

        results[sym] = LiveFleetEntry(
            symbol=sym, cohort=cohort, qvol_24h=qvol_by_symbol[sym],
            spread_bps=last_check.spread_bps, depth_0_5pct_usdt=last_check.depth_0_5pct_usdt,
        )

    # Open-position override -- re-add anything that failed exit (or was
    # never sampled this tick, e.g. dropped below the qvol pre-filter
    # entirely) but has an open position, even though the loop above
    # already excluded it. Per addendum (a) point 4 ("never removed...
    # until the position closes").
    #
    # Task 5d (ratified 2026-08-19): queries live open positions directly
    # via get_open_position_symbols, instead of only iterating prior's own
    # membership. A prior-only loop protects nothing on the table's very
    # first-ever refresh (prior is empty by definition) -- exactly the
    # cold-start / fleet-supervisor-restart case this override exists to
    # guard. Two sub-cases:
    #   - Symbol has a prior entry (steady-state: failed exit or dropped
    #     below the qvol pre-filter) -- retain prior's last-known-good
    #     numbers unchanged, not re-sampled.
    #   - Symbol has NO prior entry at all (brand-new open position, or
    #     this is the very first refresh ever) -- rather than fabricate
    #     placeholder numbers, take one fresh check_liquidity sample and
    #     tag established_top20 as the safe cohort default when there is
    #     genuinely no lineage to inherit. Self-correcting from the next
    #     refresh onward once this symbol has a prior entry. If even the
    #     rescue sample fails (e.g. a spot-only symbol with no futures
    #     order book), log loudly -- coverage genuinely cannot be
    #     guaranteed this refresh -- rather than silently drop the symbol.
    async with session_factory() as session:
        open_syms = await get_open_position_symbols(session)
        for sym in open_syms:
            if sym in results:
                continue
            if sym in prior:
                log.info("live_fleet_universe: retaining %s past exit -- open position", sym)
                results[sym] = prior[sym]  # last-known-good numbers, not re-sampled
            else:
                try:
                    check = await check_liquidity(sym, rate_client)
                    results[sym] = LiveFleetEntry(
                        symbol=sym, cohort="established_top20",
                        qvol_24h=qvol_by_symbol.get(sym, 0.0),
                        spread_bps=check.spread_bps, depth_0_5pct_usdt=check.depth_0_5pct_usdt,
                    )
                    log.warning(
                        "live_fleet_universe: %s has an open position but no prior "
                        "snapshot entry -- rescued with a fresh sample, tagged "
                        "established_top20 as the safe default", sym,
                    )
                except Exception as e:  # noqa: BLE001
                    log.error(
                        "live_fleet_universe: %s has an open position, no prior "
                        "entry, AND a fresh check_liquidity sample failed (%s) -- "
                        "coverage genuinely cannot be guaranteed this refresh", sym, e,
                    )

        now = datetime.now(timezone.utc)
        for entry in results.values():
            await session.execute(
                sa.text(
                    "INSERT INTO live_fleet_universe "
                    "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
                    "VALUES (:sym, :cohort, :qvol, :spread, :depth, :ts)"
                ),
                {"sym": entry.symbol, "cohort": entry.cohort, "qvol": entry.qvol_24h,
                 "spread": entry.spread_bps, "depth": entry.depth_0_5pct_usdt, "ts": now},
            )
        await session.commit()

    return list(results.values())


__all__ = [
    "N_SAMPLES", "SAMPLE_GAP_SECONDS", "ENTRY_MIN_PASSES", "EXIT_MAX_PASSES",
    "LiveFleetEntry", "has_open_position", "get_open_position_symbols",
    "load_live_fleet_universe", "refresh_live_fleet_universe",
]
