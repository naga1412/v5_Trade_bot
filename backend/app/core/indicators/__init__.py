"""Indicator registry — pure numeric functions over OHLCV arrays.

All indicators are NaN-leading: the first ``period - 1`` outputs are
``np.nan`` so downstream callers can ``if math.isnan(x): return None``. None of
these functions look ahead — ``output[i]`` depends only on ``input[0..i]``.

The exported ``ALL_INDICATORS`` dict maps the public function name to its
callable so admin tooling, the L2 aggregator, and the ML feature pipeline can
iterate the registry without re-listing names. Spec §3.1's "43 indicators" is
a count of *output lines* (bollinger upper/middle/lower count as 3, MACD
line/signal/hist as 3, etc.); the *module count* is 34 — 3 SP-0 baseline
(``ema``, ``macd``, ``rsi``) plus 31 SP-2 Phase B additions.
"""
from collections.abc import Callable
from typing import Any

from app.core.indicators.adx import adx
from app.core.indicators.aroon import aroon
from app.core.indicators.atr import atr
from app.core.indicators.awesome_oscillator import awesome_oscillator
from app.core.indicators.bollinger import bollinger
from app.core.indicators.cci import cci
from app.core.indicators.chaikin_money_flow import chaikin_money_flow
from app.core.indicators.dema import dema
from app.core.indicators.donchian import donchian
from app.core.indicators.dpo import dpo
from app.core.indicators.ease_of_movement import ease_of_movement
from app.core.indicators.ema import ema
from app.core.indicators.force_index import force_index
from app.core.indicators.hull_ma import hull_ma
from app.core.indicators.ichimoku import ichimoku
from app.core.indicators.kama import kama
from app.core.indicators.keltner import keltner
from app.core.indicators.kvo import kvo
from app.core.indicators.macd import macd
from app.core.indicators.mass_index import mass_index
from app.core.indicators.mfi import mfi
from app.core.indicators.obv import obv
from app.core.indicators.psar import psar
from app.core.indicators.roc import roc
from app.core.indicators.rsi import rsi
from app.core.indicators.sma import sma
from app.core.indicators.stochastic import stochastic
from app.core.indicators.tema import tema
from app.core.indicators.trix import trix
from app.core.indicators.tsi import tsi
from app.core.indicators.ultimate import ultimate
from app.core.indicators.vortex import vortex
from app.core.indicators.vwap import vwap
from app.core.indicators.williams_r import williams_r

ALL_INDICATORS: dict[str, Callable[..., Any]] = {
    "adx": adx,
    "aroon": aroon,
    "atr": atr,
    "awesome_oscillator": awesome_oscillator,
    "bollinger": bollinger,
    "cci": cci,
    "chaikin_money_flow": chaikin_money_flow,
    "dema": dema,
    "donchian": donchian,
    "dpo": dpo,
    "ease_of_movement": ease_of_movement,
    "ema": ema,
    "force_index": force_index,
    "hull_ma": hull_ma,
    "ichimoku": ichimoku,
    "kama": kama,
    "keltner": keltner,
    "kvo": kvo,
    "macd": macd,
    "mass_index": mass_index,
    "mfi": mfi,
    "obv": obv,
    "psar": psar,
    "roc": roc,
    "rsi": rsi,
    "sma": sma,
    "stochastic": stochastic,
    "tema": tema,
    "trix": trix,
    "tsi": tsi,
    "ultimate": ultimate,
    "vortex": vortex,
    "vwap": vwap,
    "williams_r": williams_r,
}

__all__ = [
    "ALL_INDICATORS",
    *sorted(ALL_INDICATORS.keys()),
]
