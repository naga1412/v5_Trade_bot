"""Slow, env-gated FinBERT performance test (SP-9 Phase C3).

Asserts a per-batch latency budget so we catch regressions where someone
accidentally disables ``torch.no_grad()``, drops batching, or pulls in a
heavier model. Gated on ``RUN_FINBERT_SLOW_TESTS=1`` for the same reasons
as the C2 smoke test.

To run locally:

.. code-block:: bash

    RUN_FINBERT_SLOW_TESTS=1 pytest tests/unit/test_news_sentiment_perf.py -m slow
"""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("transformers")
pytest.importorskip("torch")

from app.news.sentiment import classify_batch  # noqa: E402

_GATE = "RUN_FINBERT_SLOW_TESTS"


def _gated() -> None:
    if os.environ.get(_GATE) != "1":
        pytest.skip(f"set {_GATE}=1 to run FinBERT slow tests")


@pytest.mark.slow
def test_finbert_batch16_latency_under_10s() -> None:
    """A single batch-of-16 forward pass must complete in < 10s on CPU.

    The string is deliberately long (100 chars) to stress tokenization and
    push the input length closer to the ``max_length=128`` truncation cap.
    """
    _gated()
    titles = ["x" * 100] * 16
    # Warm-up call to absorb the lazy model load — we're measuring inference
    # latency, not the one-time disk read of the cached weights.
    classify_batch(titles[:1])

    start = time.perf_counter()
    results = classify_batch(titles)
    elapsed = time.perf_counter() - start
    assert len(results) == 16
    assert elapsed < 10.0, (
        f"FinBERT batch-16 inference took {elapsed:.2f}s (budget: 10.0s)"
    )
