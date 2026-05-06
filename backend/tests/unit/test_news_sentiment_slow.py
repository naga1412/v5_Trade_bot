"""Slow, env-gated FinBERT smoke test (SP-9 Phase C2).

Exercises the **real** ``ProsusAI/finbert`` model on a small set of
hand-crafted crypto headlines. The first run downloads ~440MB to
``~/.cache/huggingface/`` and takes several seconds on CPU, so this test is
gated on the ``RUN_FINBERT_SLOW_TESTS`` environment variable to keep CI
fast. To run locally:

.. code-block:: bash

    RUN_FINBERT_SLOW_TESTS=1 pytest tests/unit/test_news_sentiment_slow.py -m slow

Both ``transformers`` and ``torch`` are required; the file uses
``pytest.importorskip`` so a stripped-down environment without the heavy ML
stack still passes ``pytest``.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("transformers")
pytest.importorskip("torch")

from app.news.sentiment import classify_batch  # noqa: E402

_GATE = "RUN_FINBERT_SLOW_TESTS"


def _gated() -> None:
    if os.environ.get(_GATE) != "1":
        pytest.skip(f"set {_GATE}=1 to run FinBERT slow tests")


# (title, expected_label) — picked so FinBERT, which was trained on financial
# news, should produce the obvious sign with high confidence.
_SMOKE_HEADLINES: list[tuple[str, str]] = [
    ("Bitcoin surges to all-time high", "positive"),
    ("SEC announces crackdown on crypto exchanges", "negative"),
    ("Ethereum upgrades launch successfully", "positive"),
    ("Major exchange hacked, $300M lost", "negative"),
    ("Bitcoin holds steady at $80,000", "neutral"),
]


@pytest.mark.slow
def test_finbert_smoke_five_headlines() -> None:
    """End-to-end FinBERT classification of 5 hand-crafted crypto headlines.

    Each result must:
      * have ``label`` matching the obvious sign,
      * carry non-trivial confidence (> 0.4 — FinBERT was trained on
        equities/bonds news, so crypto-specific phrasings can be borderline),
      * have a ``score`` consistent with the label sign.
    """
    _gated()
    titles = [t for t, _ in _SMOKE_HEADLINES]
    results = classify_batch(titles)
    assert len(results) == len(_SMOKE_HEADLINES)
    for (title, expected_label), result in zip(
        _SMOKE_HEADLINES, results, strict=True
    ):
        assert result.label == expected_label, (
            f"FinBERT mis-labelled {title!r}: "
            f"expected {expected_label}, got {result.label} "
            f"(score={result.score:.3f}, conf={result.confidence:.3f})"
        )
        assert result.confidence > 0.4
        assert -1.0 <= result.score <= 1.0
        if expected_label == "positive":
            assert result.score > 0
        elif expected_label == "negative":
            assert result.score < 0
