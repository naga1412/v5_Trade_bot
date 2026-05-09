"""SP-8 Phase F — live exchange order placement clients.

Separate package from app/data/adapters/ (which is read-only kline +
universe data) so the live-money write path is auditable in isolation.
"""
