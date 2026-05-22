"""Per-asset learned embedding + cold-start blending (SP-4 Phase A5).

Spec sec D2 + Q5 — each universe asset gets a learned 32-dim embedding
that the policy network concatenates onto the observation vector.
For NEW assets (no trade history) we blend the median of all known
embeddings into the asset's own learned embedding linearly over its
first ``COLD_START_TRADES`` trades:

    α = min(1.0, n_trades / COLD_START_TRADES)
    effective_emb = (1 - α) * median_known + α * learned_for_this_asset

This gives the brain "the average asset" right away on a new listing,
then phases in per-asset specialization as evidence accumulates.

The :class:`AssetEmbeddingTable` is a thin wrapper around
``nn.Embedding(N, 32)`` with a :meth:`register_asset` API for adding
new symbols at runtime + a save/load pair for checkpoint persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import torch
from torch import nn

from app.rl.obs import EMB_DIM


COLD_START_TRADES: int = 100  # spec sec Q5
INITIAL_TABLE_SIZE: int = 1024  # cap on assets we ever expect; sized for v1 + headroom


@dataclass
class AssetEmbeddingTable:
    """Mutable per-asset embedding store.

    Wrapping ``nn.Embedding`` directly means PPO's optimizer auto-trains
    the embeddings as part of the policy. ``symbol_to_id`` is the
    persistent integer-id map (saved in the checkpoint alongside the
    weights so reload is bit-for-bit reproducible).
    """

    n_slots: int = INITIAL_TABLE_SIZE
    emb_dim: int = EMB_DIM
    symbol_to_id: dict[str, int] = field(default_factory=dict)
    _module: nn.Embedding = field(init=False)

    def __post_init__(self) -> None:
        # Initialize from a small zero-mean Gaussian so the network sees
        # a valid signal from epoch 1 instead of all-zeros (which would
        # waste warm-up time learning the embedding off the zero vector).
        self._module = nn.Embedding(self.n_slots, self.emb_dim)
        with torch.no_grad():
            nn.init.normal_(self._module.weight, mean=0.0, std=0.05)

    def register_asset(self, symbol: str) -> int:
        """Allocate a new slot for ``symbol`` if not already known.

        New embeddings are seeded with the median of all currently-known
        embeddings (cold-start prior; spec sec Q5). Returns the integer
        id assigned to the symbol.
        """
        if symbol in self.symbol_to_id:
            return self.symbol_to_id[symbol]

        next_id = len(self.symbol_to_id)
        if next_id >= self.n_slots:
            raise RuntimeError(
                f"AssetEmbeddingTable full: {self.n_slots} slots used"
            )
        self.symbol_to_id[symbol] = next_id

        if next_id > 0:
            # Seed from median of known embeddings; cold-start blend will
            # handle the per-trade ramp at inference time.
            with torch.no_grad():
                known = self._module.weight[:next_id]
                median = known.median(dim=0).values
                self._module.weight[next_id] = median
        return next_id

    def bulk_register(self, symbols: Iterable[str]) -> None:
        """Pre-register many symbols WITHOUT median-seeding their embeddings.

        Differs from :meth:`register_asset`: we preserve the independent
        Gaussian-init weight that ``__post_init__`` placed in each slot,
        so the brain sees diverse per-asset signal during training.

        ``register_asset`` is meant for INFERENCE-time unseen-symbol entry
        (median-seed gives an "average asset" cold-start). Using it in a
        tight training-time loop collapses all embeddings to a single
        cluster near the first asset's Gaussian sample — which is what
        produced the degenerate ``rl_checkpoints.id=1`` state.

        Already-registered symbols are skipped (idempotent).
        """
        for symbol in symbols:
            if symbol in self.symbol_to_id:
                continue
            next_id = len(self.symbol_to_id)
            if next_id >= self.n_slots:
                raise RuntimeError(
                    f"AssetEmbeddingTable full: {self.n_slots} slots used"
                )
            self.symbol_to_id[symbol] = next_id
            # Intentionally NO weight overwrite — preserves the Gaussian
            # init from __post_init__.

    def get_embedding(
        self, symbol: str, *, n_trades_for_asset: int = 0,
    ) -> np.ndarray:
        """Return the 32-dim embedding for ``symbol`` as numpy float32.

        Applies the cold-start blend if this asset has fewer than
        ``COLD_START_TRADES`` closed trades; otherwise returns the raw
        learned embedding. Caller passes the trade count from the
        ``shadow_trades`` aggregate.
        """
        if symbol not in self.symbol_to_id:
            self.register_asset(symbol)
        idx = self.symbol_to_id[symbol]
        with torch.no_grad():
            learned = self._module.weight[idx].detach().cpu().numpy().astype(np.float32)
        if n_trades_for_asset >= COLD_START_TRADES or len(self.symbol_to_id) <= 1:
            return learned

        with torch.no_grad():
            n_known = len(self.symbol_to_id)
            slice_ = self._module.weight[:n_known].detach().cpu().numpy()
            median = np.median(slice_, axis=0).astype(np.float32)
        return cold_start_blend(median, learned, n_trades_for_asset)

    def state_dict(self) -> dict:
        """Returns a serializable representation: weights tensor + id map."""
        return {
            "weights": self._module.state_dict(),
            "symbol_to_id": dict(self.symbol_to_id),
            "n_slots": self.n_slots,
            "emb_dim": self.emb_dim,
        }

    def load_state_dict(self, state: dict) -> None:
        """Restore from a previously-saved state_dict."""
        self.n_slots = int(state["n_slots"])
        self.emb_dim = int(state["emb_dim"])
        self.symbol_to_id = dict(state["symbol_to_id"])
        self._module = nn.Embedding(self.n_slots, self.emb_dim)
        self._module.load_state_dict(state["weights"])

    @property
    def module(self) -> nn.Embedding:
        return self._module


def cold_start_blend(
    median_emb: np.ndarray, learned_emb: np.ndarray, n_trades: int,
) -> np.ndarray:
    """Linear blend per spec sec Q5: α = min(1, n_trades / COLD_START_TRADES).

    Returns ``(1 - α) * median + α * learned``. Both inputs must be
    same-shape float32 arrays; result is float32.
    """
    if median_emb.shape != learned_emb.shape:
        raise ValueError(
            f"shape mismatch: median {median_emb.shape} vs "
            f"learned {learned_emb.shape}"
        )
    alpha = min(1.0, max(0.0, n_trades / COLD_START_TRADES))
    return (
        (1.0 - alpha) * median_emb.astype(np.float32)
        + alpha * learned_emb.astype(np.float32)
    ).astype(np.float32)


__all__ = [
    "COLD_START_TRADES",
    "INITIAL_TABLE_SIZE",
    "AssetEmbeddingTable",
    "cold_start_blend",
]
