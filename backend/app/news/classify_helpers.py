"""Stub — implemented in SP-9 Phase B3/B4."""
from __future__ import annotations


def extract_affected_assets(title: str) -> tuple[str, ...]:
    raise NotImplementedError("SP-9 Phase B3")


def classify_category(title: str) -> str | None:
    raise NotImplementedError("SP-9 Phase B4")


def impact_score_for(category: str | None, source: str) -> float:
    raise NotImplementedError("SP-9 Phase B5")
