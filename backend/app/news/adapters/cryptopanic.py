"""Stub — implemented in SP-9 Phase B1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.news.adapters._base import NewsArticle


@dataclass
class CryptoPanicAdapter:
    name: str = "cryptopanic"

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        raise NotImplementedError("SP-9 Phase B1")
