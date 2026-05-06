"""SP-9 Phase F1: SentimentSummary + NewsSummary + LivePredictionOut.

Backward-compatible extension: both new fields are Optional with default
``None`` so legacy payloads (no sentiment/news block) parse unchanged.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    LivePredictionOut,
    NewsSummary,
    SentimentSummary,
)


def test_sentiment_summary_valid_labels() -> None:
    s = SentimentSummary(fng_value=50, fng_label="Neutral", news_bias="Neutral")
    assert s.fng_value == 50
    assert s.fng_label == "Neutral"
    assert s.news_bias == "Neutral"


def test_sentiment_summary_invalid_label_rejected() -> None:
    with pytest.raises(ValidationError):
        SentimentSummary(  # type: ignore[arg-type]
            fng_value=50, fng_label="Banana", news_bias="Neutral",
        )


def test_sentiment_summary_value_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        SentimentSummary(fng_value=-1, fng_label="Fear", news_bias="Neutral")
    with pytest.raises(ValidationError):
        SentimentSummary(fng_value=101, fng_label="Greed", news_bias="Neutral")


def test_news_summary_impact_literal() -> None:
    n = NewsSummary(recent_count=3, top_headline="X", impact="HIGH")
    assert n.impact == "HIGH"
    assert n.top_headline == "X"
    with pytest.raises(ValidationError):
        NewsSummary(  # type: ignore[arg-type]
            recent_count=0, top_headline=None, impact="ENORMOUS",
        )


def test_news_summary_top_headline_optional() -> None:
    n = NewsSummary(recent_count=0, top_headline=None, impact="LOW")
    assert n.top_headline is None
    assert n.recent_count == 0


def test_live_prediction_out_optional_sentiment_news() -> None:
    """Existing minimal payload still validates with sentiment/news omitted."""
    payload = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": "2026-05-06T12:00:00Z",
        "price": 100.0,
        "final": {
            "score": 0.0, "direction": "NEUTRAL", "confidence": 0.0,
            "contributing_layers": [],
        },
        "layer_scores": {},
        "trade_setup": {"direction": "NEUTRAL"},
        "momentum": {
            "rsi": None, "macd_line": None,
            "macd_signal": None, "macd_hist": None,
        },
        "cold_start": True,
        "inputs_hash": "x",
    }
    out = LivePredictionOut(**payload)
    assert out.sentiment is None
    assert out.news is None


def test_live_prediction_out_round_trip_with_sentiment_news() -> None:
    """Round-trip parse + serialize a payload that includes both fields."""
    payload = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": "2026-05-06T12:00:00Z",
        "price": 100.0,
        "final": {
            "score": 0.42, "direction": "LONG", "confidence": 0.7,
            "contributing_layers": [1, 9],
        },
        "layer_scores": {},
        "trade_setup": {"direction": "LONG", "entry": 100.0},
        "momentum": {
            "rsi": 55.0, "macd_line": 0.1,
            "macd_signal": 0.05, "macd_hist": 0.05,
        },
        "cold_start": False,
        "inputs_hash": "abc",
        "sentiment": {
            "fng_value": 70, "fng_label": "Greed", "news_bias": "Bullish",
        },
        "news": {
            "recent_count": 5, "top_headline": "ETF approved",
            "impact": "HIGH",
        },
    }
    out = LivePredictionOut(**payload)
    assert out.sentiment is not None and out.sentiment.fng_value == 70
    assert out.news is not None and out.news.impact == "HIGH"
    # Round trip via JSON-serialisable dict.
    again = LivePredictionOut(**out.model_dump(mode="json"))
    assert again.sentiment == out.sentiment
    assert again.news == out.news
