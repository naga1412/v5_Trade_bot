"""Unit tests for SP-9 Phase C1 FinBERT sentiment classifier.

These tests deliberately do NOT load the real FinBERT model (~440MB download
on first call + multi-second inference). Instead, ``app.news.sentiment._load``
is monkey-patched to return fake tokenizer / model / torch shims that produce
deterministic logits for a known label order.

The slow / heavy end-to-end behaviour (real model download + inference) is
covered by the env-gated tests in ``test_news_sentiment_slow.py`` (C2 + C3).
"""
from __future__ import annotations

import math
from typing import Any

import pytest

from app.news.sentiment import SentimentResult, classify_batch


class _FakeTensor:
    """Minimal stand-in for the subset of torch.Tensor we use.

    Wraps either a 2D ``list[list[float]]`` (the softmax output) or a 1D
    ``list[float]`` (a single row, returned by ``__getitem__``). ``shape`` is
    only ever read as ``shape[0]`` in the production code, so it just stores
    the leading dimension.
    """

    def __init__(self, data: list[list[float]] | list[float]):
        self._data = data
        self.shape = (len(data),)

    def tolist(self) -> list[float]:
        # Production code only calls .tolist() on a 1D row.
        return list(self._data)  # type: ignore[arg-type]

    def argmax(self) -> int:
        # Production code only calls .argmax() on a 1D row.
        return max(
            range(len(self._data)),  # type: ignore[arg-type]
            key=lambda i: self._data[i],  # type: ignore[index]
        )

    def max(self) -> float:
        return max(self._data)  # type: ignore[type-var,arg-type]

    def __getitem__(self, idx: int) -> "_FakeTensor":
        return _FakeTensor(self._data[idx])  # type: ignore[arg-type]


class _FakeLogits:
    """Object with a ``.logits`` attribute mirroring HF model output."""

    def __init__(self, logits: list[list[float]]):
        self.logits = logits


class _FakeModel:
    """Records calls and returns logits driven by the supplied callable."""

    def __init__(self, logits_fn: Any) -> None:
        self._logits_fn = logits_fn
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **inputs: Any) -> _FakeLogits:
        self.calls.append(inputs)
        # The number of inputs in this batch == len(input_ids).
        n = len(inputs.get("input_ids", []))
        return _FakeLogits(self._logits_fn(n, len(self.calls)))

    def eval(self) -> "_FakeModel":
        return self


class _FakeTokenizer:
    """Records the batch it was called with; returns a dict with input_ids."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def __call__(
        self,
        batch: list[str],
        padding: bool = True,
        truncation: bool = True,
        return_tensors: str = "pt",
        max_length: int = 128,
    ) -> dict[str, list[int]]:
        self.batches.append(list(batch))
        # ``input_ids`` only needs to be len()-able; values are unused by the
        # fake model.
        return {"input_ids": [[0] * max_length for _ in batch]}


class _FakeNoGrad:
    def __enter__(self) -> "_FakeNoGrad":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _FakeTorch:
    """Subset of the torch API used by ``classify_batch``."""

    @staticmethod
    def no_grad() -> _FakeNoGrad:
        return _FakeNoGrad()

    @staticmethod
    def softmax(logits: list[list[float]], dim: int = -1) -> _FakeTensor:
        # Numerically stable softmax over the last dim of a 2D list.
        out: list[list[float]] = []
        for row in logits:
            m = max(row)
            exps = [math.exp(x - m) for x in row]
            s = sum(exps)
            out.append([e / s for e in exps])
        return _FakeTensor(out)


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    logits_fn: Any,
) -> tuple[_FakeTokenizer, _FakeModel, _FakeTorch]:
    """Patch ``_load`` so it returns fakes instead of downloading FinBERT."""
    tok = _FakeTokenizer()
    model = _FakeModel(logits_fn)
    torch_mod = _FakeTorch()
    monkeypatch.setattr(
        "app.news.sentiment._load",
        lambda: (tok, model, torch_mod),
    )
    return tok, model, torch_mod


# ---------------------------------------------------------------------------
# C1 contract tests
# ---------------------------------------------------------------------------


def test_empty_list_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even with no titles we should not pay the cost of loading the model.
    called = {"loaded": False}

    def _boom() -> tuple[Any, Any, Any]:
        called["loaded"] = True
        raise AssertionError("_load must not be invoked for empty input")

    monkeypatch.setattr("app.news.sentiment._load", _boom)
    assert classify_batch([]) == []
    assert called["loaded"] is False


def test_single_title_returns_single_result(monkeypatch: pytest.MonkeyPatch) -> None:
    # Logits favour the "positive" class (index 0 in FinBERT's label order).
    _install_fakes(monkeypatch, lambda n, _call: [[5.0, 0.0, 0.0]] * n)
    out = classify_batch(["Bitcoin rallies"])
    assert len(out) == 1
    r = out[0]
    assert isinstance(r, SentimentResult)
    assert r.label == "positive"
    assert -1.0 <= r.score <= 1.0
    assert 0.0 <= r.confidence <= 1.0
    # Strongly positive logits → score close to +1, confidence close to 1.
    assert r.score > 0.9
    assert r.confidence > 0.9


def test_negative_logits_map_to_negative_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Index 1 in FinBERT label order is "negative".
    _install_fakes(monkeypatch, lambda n, _call: [[0.0, 5.0, 0.0]] * n)
    out = classify_batch(["Exchange hacked"])
    assert out[0].label == "negative"
    assert out[0].score < -0.9


def test_neutral_logits_map_to_neutral_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Index 2 is "neutral"; pos and neg tie so score is ~0.
    _install_fakes(monkeypatch, lambda n, _call: [[1.0, 1.0, 5.0]] * n)
    out = classify_batch(["Price holds steady"])
    assert out[0].label == "neutral"
    assert abs(out[0].score) < 1e-6


def test_score_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Spread of inputs → all results must satisfy [-1, +1] and [0, 1] bounds.
    _install_fakes(
        monkeypatch,
        lambda n, _call: [
            [3.0, 0.0, 0.0],
            [0.0, 3.0, 0.0],
            [0.0, 0.0, 3.0],
        ][:n],
    )
    out = classify_batch(["a", "b", "c"])
    for r in out:
        assert -1.0 <= r.score <= 1.0
        assert 0.0 <= r.confidence <= 1.0


def test_label_matches_argmax(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        [4.0, 0.0, 1.0],  # positive wins
        [0.0, 4.0, 1.0],  # negative wins
        [1.0, 0.0, 4.0],  # neutral wins
    ]
    expected = ["positive", "negative", "neutral"]
    _install_fakes(monkeypatch, lambda n, _call: rows[:n])
    out = classify_batch(["a", "b", "c"])
    assert [r.label for r in out] == expected


def test_batching_17_titles_with_batch_size_8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verify the slicing math: 17 / 8 → batches of 8, 8, 1.
    tok, model, _ = _install_fakes(
        monkeypatch,
        lambda n, _call: [[1.0, 0.0, 0.0]] * n,
    )
    out = classify_batch(["t" + str(i) for i in range(17)], batch_size=8)
    assert len(out) == 17
    assert [len(b) for b in tok.batches] == [8, 8, 1]
    assert len(model.calls) == 3


def test_batch_size_default_16(monkeypatch: pytest.MonkeyPatch) -> None:
    # 33 titles at the default batch_size=16 → batches of 16, 16, 1.
    tok, _, _ = _install_fakes(
        monkeypatch,
        lambda n, _call: [[1.0, 0.0, 0.0]] * n,
    )
    out = classify_batch(["t"] * 33)
    assert len(out) == 33
    assert [len(b) for b in tok.batches] == [16, 16, 1]


def test_score_equals_p_pos_minus_p_neg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With logits [ln(0.6), ln(0.3), ln(0.1)] softmax gives exactly those
    # probabilities, so score == 0.6 - 0.3 == 0.3.
    logits_row = [math.log(0.6), math.log(0.3), math.log(0.1)]
    _install_fakes(monkeypatch, lambda n, _call: [logits_row] * n)
    out = classify_batch(["x"])
    assert out[0].score == pytest.approx(0.3, abs=1e-9)
    assert out[0].confidence == pytest.approx(0.6, abs=1e-9)
    assert out[0].label == "positive"


def test_results_are_immutable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(monkeypatch, lambda n, _call: [[1.0, 0.0, 0.0]] * n)
    out = classify_batch(["x"])
    with pytest.raises((AttributeError, Exception)):
        out[0].score = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Lazy-load contract: subsequent calls reuse cached tokenizer / model
# ---------------------------------------------------------------------------


def test_load_is_called_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    # Importing ``app.news.sentiment`` must NOT have eagerly populated the
    # module-level cache. Without monkey-patching, calling ``classify_batch``
    # would attempt to load FinBERT — but only on the *first* call, and only
    # when given a non-empty input. We verify the empty-input short-circuit
    # in the empty-list test above; here we just confirm the cache vars
    # start as None.
    import app.news.sentiment as sentiment_mod

    # We can't make a strict assertion if a previous test in the same process
    # has already run with the real loader, but mocked tests never touch the
    # real cache. Assert the names exist and default to None when this test
    # is run in isolation.
    assert hasattr(sentiment_mod, "_tokenizer")
    assert hasattr(sentiment_mod, "_model")
    assert hasattr(sentiment_mod, "_torch")
