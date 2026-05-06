"""Stub — implemented in SP-9 Phase D2/D3."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def run_news_ingest_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raise NotImplementedError("SP-9 Phase D2")


async def run_news_cleanup_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raise NotImplementedError("SP-9 Phase D3")


def start_news_ingest_task(session_factory) -> asyncio.Task:  # type: ignore[no-untyped-def]
    raise NotImplementedError("SP-9 Phase D4")


def start_news_cleanup_task(session_factory) -> asyncio.Task:  # type: ignore[no-untyped-def]
    raise NotImplementedError("SP-9 Phase D4")
