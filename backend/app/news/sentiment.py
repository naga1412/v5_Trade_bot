"""FinBERT sentiment classifier (SP-9 Phase C1).

Wraps the ``ProsusAI/finbert`` HuggingFace model behind a thin batch API.

Design notes
------------
* The tokenizer / model / torch handle are loaded **lazily** on the first call
  to :func:`classify_batch`. This keeps the ~440MB FinBERT download out of
  module-import time so callers that never use sentiment (most CLI tools and
  unit tests) do not pay for it.
* ``torch`` is imported inside :func:`_load` for the same reason — it's a
  multi-hundred-MB import that should not be mandatory for the rest of the
  app to start.
* The label order returned by ``ProsusAI/finbert`` is
  ``[positive, negative, neutral]`` (this differs from some other FinBERT
  forks, so it is encoded explicitly here).
* Tests mock :func:`_load` rather than invoking the real model — see
  ``tests/unit/test_news_sentiment.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Module-level cache populated on first call to ``_load``. Keep these typed
# as ``Any`` so the lazy ``transformers`` / ``torch`` imports don't leak into
# this module's import surface.
_tokenizer: Any | None = None
_model: Any | None = None
_torch: Any | None = None


@dataclass(frozen=True)
class SentimentResult:
    """Output of FinBERT inference for a single headline.

    Attributes
    ----------
    score:
        ``p_positive - p_negative`` — bounded in ``[-1, +1]``. Negative values
        indicate bearish sentiment; positive values bullish.
    label:
        Argmax over ``[positive, negative, neutral]``.
    confidence:
        ``max(softmax(logits))`` — bounded in ``[0, 1]``.
    """

    score: float
    label: Literal["positive", "negative", "neutral"]
    confidence: float


def _load() -> tuple[Any, Any, Any]:
    """Lazy-load FinBERT tokenizer, model, and the ``torch`` module.

    First call downloads the model (~440MB) to ``~/.cache/huggingface/`` and
    primes the module-level cache. Subsequent calls return the cached refs.
    """
    global _tokenizer, _model, _torch
    if _tokenizer is None:
        import torch
        from transformers import (  # type: ignore[import-untyped]
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        _tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
        _model = AutoModelForSequenceClassification.from_pretrained(
            "ProsusAI/finbert"
        )
        _model.eval()
        _torch = torch
    return _tokenizer, _model, _torch


# FinBERT's label order — encoded once so the inference loop reads cleanly.
_LABELS: tuple[Literal["positive", "negative", "neutral"], ...] = (
    "positive",
    "negative",
    "neutral",
)


def classify_batch(
    titles: list[str],
    batch_size: int = 16,
) -> list[SentimentResult]:
    """Run FinBERT inference on a list of headlines.

    Parameters
    ----------
    titles:
        Headline strings to classify. Empty list returns an empty list
        without loading the model.
    batch_size:
        Number of titles per forward pass. Defaults to 16, which keeps the
        per-batch tensor small enough to fit comfortably on CPU while
        amortising the per-call overhead of the tokenizer.

    Returns
    -------
    One :class:`SentimentResult` per input title, in the same order.
    """
    if not titles:
        return []
    tokenizer, model, torch = _load()
    results: list[SentimentResult] = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i : i + batch_size]
        inputs = tokenizer(
            batch,
            padding=True,
            truncation=True,
            return_tensors="pt",
            max_length=128,
        )
        with torch.no_grad():
            logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)
        for j in range(probs.shape[0]):
            row = probs[j]
            p_pos, p_neg, _p_neu = row.tolist()
            score = float(p_pos - p_neg)
            label_idx = int(row.argmax())
            label = _LABELS[label_idx]
            confidence = float(row.max())
            results.append(
                SentimentResult(
                    score=score,
                    label=label,
                    confidence=confidence,
                )
            )
    return results
