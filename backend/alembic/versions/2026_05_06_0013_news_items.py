"""news_items table for SP-9 News + Sentiment

Revision ID: 0013_news_items
Revises: 0012_backtests_hyperopt_backups
Create Date: 2026-05-06
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0013_news_items"
down_revision: str | None = "0012_backtests_hyperopt_backups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        # Postgres production schema: ARRAY column + GIN index for the L9
        # `ANY(affected_assets)` lookup at scale (spec §4.1).
        op.execute(
            """
            CREATE TABLE news_items (
                id BIGSERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                published_at TIMESTAMPTZ NOT NULL,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sentiment_score DOUBLE PRECISION,
                sentiment_label TEXT
                    CHECK (sentiment_label IN ('positive','negative','neutral')),
                sentiment_confidence DOUBLE PRECISION,
                impact_score DOUBLE PRECISION,
                category TEXT
                    CHECK (category IN
                        ('regulatory','exchange','macro','whale','project','social')),
                affected_assets TEXT[]
            );
            """
        )
        op.execute(
            "CREATE INDEX news_items_published_idx "
            "ON news_items (published_at DESC);"
        )
        op.execute(
            "CREATE INDEX news_items_assets_gin_idx "
            "ON news_items USING GIN (affected_assets);"
        )
        op.execute(
            "CREATE INDEX news_items_source_published_idx "
            "ON news_items (source, published_at DESC);"
        )
    else:
        # SQLite (test fixture) mirror: TEXT[] is collapsed to a comma-
        # separated TEXT column; the GIN index is omitted (SQLite has no
        # GIN). The L9 query path uses the persistence-layer helper to
        # convert between tuple <-> CSV at the boundary.
        op.execute(
            """
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                published_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
                sentiment_score REAL,
                sentiment_label TEXT
                    CHECK (sentiment_label IN ('positive','negative','neutral')),
                sentiment_confidence REAL,
                impact_score REAL,
                category TEXT
                    CHECK (category IN
                        ('regulatory','exchange','macro','whale','project','social')),
                affected_assets TEXT
            );
            """
        )
        op.execute(
            "CREATE INDEX news_items_published_idx "
            "ON news_items (published_at DESC);"
        )
        op.execute(
            "CREATE INDEX news_items_source_published_idx "
            "ON news_items (source, published_at DESC);"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS news_items_source_published_idx;")
    op.execute("DROP INDEX IF EXISTS news_items_assets_gin_idx;")
    op.execute("DROP INDEX IF EXISTS news_items_published_idx;")
    op.execute("DROP TABLE IF EXISTS news_items;")
