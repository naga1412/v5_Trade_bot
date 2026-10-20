"""PR-BRAIN-WARMSTART-FIX — train_brain.py helpers for ordered asset_table init.

Tests the two helpers introduced by the warm-start ordering fix:

  - _discover_training_symbols: returns distinct symbols from shadow_trades
  - _extract_asset_embeddings: snapshots {asset_id: ndarray} from AssetEmbeddingTable
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Insert tools/ml/ on sys.path so we can import train_brain helpers directly.
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "tools" / "ml"))

import train_brain  # noqa: E402

from app.rl.adapter import AssetEmbeddingTable  # noqa: E402


CREATE_SHADOW_TRADES = (
    "CREATE TABLE shadow_trades ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
    "direction TEXT NOT NULL, entry_price REAL NOT NULL, "
    "stop_loss REAL NOT NULL, take_profit REAL NOT NULL, "
    "position_size_usdt REAL NOT NULL, entry_score REAL NOT NULL, "
    "entry_confidence REAL NOT NULL, layer_scores TEXT NOT NULL, "
    "entry_atr REAL NOT NULL, exit_price REAL, exit_reason TEXT, "
    "pnl_pct REAL, pnl_usdt REAL, bars_held INTEGER, "
    "opened_at TEXT NOT NULL, closed_at TEXT, inputs_hash TEXT NOT NULL, "
    "model_version TEXT, signal_id TEXT, prev_hash TEXT, row_hash TEXT)"
)


async def _insert_trade(
    session: AsyncSession,
    *,
    symbol: str,
    pnl_usdt: float | None,
    closed_at: str | None,
) -> None:
    await session.execute(sa.text(
        "INSERT INTO shadow_trades "
        "(symbol, direction, entry_price, stop_loss, take_profit, "
        " position_size_usdt, entry_score, entry_confidence, layer_scores, "
        " entry_atr, pnl_usdt, opened_at, closed_at, inputs_hash, "
        " model_version, signal_id, prev_hash, row_hash) "
        "VALUES (:sym, 'LONG', 100.0, 99.0, 102.0, 10.0, 0.5, 0.6, '{}', "
        " 1.0, :pnl, '2026-05-14T17:00:00+00:00', :closed, 'hash', "
        " 'v', 'sig', 'p', 'r')"
    ), {"sym": symbol, "pnl": pnl_usdt, "closed": closed_at})


@pytest_asyncio.fixture
async def db_url(tmp_path: Path):
    """Yield an async sqlite URL for an empty shadow_trades DB."""
    dbfile = tmp_path / "test_train.db"
    url = f"sqlite+aiosqlite:///{dbfile}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_SHADOW_TRADES))
    await engine.dispose()
    yield url


@pytest.mark.asyncio
async def test_discover_training_symbols_returns_distinct_sorted(
    db_url: str,
) -> None:
    """The helper must dedupe + sort + filter to closed trades with pnl."""
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        # 3 distinct symbols, two of them with multiple trades, plus a
        # never-closed trade that must be filtered out.
        await _insert_trade(s, symbol="ETHUSDT", pnl_usdt=1.0, closed_at="2026-05-15T00:00:00Z")
        await _insert_trade(s, symbol="ETHUSDT", pnl_usdt=2.0, closed_at="2026-05-16T00:00:00Z")
        await _insert_trade(s, symbol="ADAUSDT", pnl_usdt=-0.5, closed_at="2026-05-17T00:00:00Z")
        await _insert_trade(s, symbol="BTCUSDT", pnl_usdt=3.0, closed_at="2026-05-18T00:00:00Z")
        await _insert_trade(s, symbol="OPEN_TRADE", pnl_usdt=None, closed_at=None)
        await s.commit()
    await engine.dispose()

    symbols = await train_brain._discover_training_symbols(db_url)

    assert symbols == ["ADAUSDT", "BTCUSDT", "ETHUSDT"]


@pytest.mark.asyncio
async def test_discover_training_symbols_returns_empty_when_no_closed_trades(
    db_url: str,
) -> None:
    """No closed trades → empty list (not an error)."""
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        await _insert_trade(s, symbol="X", pnl_usdt=None, closed_at=None)
        await s.commit()
    await engine.dispose()

    symbols = await train_brain._discover_training_symbols(db_url)
    assert symbols == []


def test_extract_asset_embeddings_round_trip() -> None:
    """Snapshot returns a dict keyed by asset_id with the table's weights."""
    table = AssetEmbeddingTable()
    table.bulk_register(["BTCUSDT", "ETHUSDT", "ADAUSDT"])

    out = train_brain._extract_asset_embeddings(table)

    assert set(out.keys()) == {0, 1, 2}
    for vec in out.values():
        assert isinstance(vec, np.ndarray)
        assert vec.shape == (32,)
        assert vec.dtype == np.float32

    # Values must match the table's actual weights byte-for-byte.
    expected_w = table.module.weight.detach().cpu().numpy().astype(np.float32)
    for asset_id in [0, 1, 2]:
        np.testing.assert_array_equal(out[asset_id], expected_w[asset_id])


def test_extract_asset_embeddings_returns_diverse_embeddings_for_cold_start() -> None:
    """After bulk_register, the extracted embeddings are NOT identical
    across slots. Pins the bug fix: id=1 had cluster-near-Gaussian, this
    test asserts the post-fix output has real per-asset diversity."""
    import torch
    torch.manual_seed(42)  # determinism for the randomness-based assertion
    table = AssetEmbeddingTable()
    table.bulk_register([f"SYM{i:02d}" for i in range(10)])

    out = train_brain._extract_asset_embeddings(table)
    stacked = np.stack([out[i] for i in range(10)])
    # Per-coordinate std across the 10 vectors should be near the init std (0.05).
    per_coord_std = stacked.std(axis=0)
    assert float(per_coord_std.mean()) > 0.01, (
        "embeddings clustered too tightly — bulk_register may have lost its "
        "preserve-init guarantee"
    )


def test_extract_asset_embeddings_returns_empty_for_unregistered_table() -> None:
    """A fresh table with no symbols registered yields an empty dict."""
    table = AssetEmbeddingTable()
    out = train_brain._extract_asset_embeddings(table)
    assert out == {}


# ---------------------------------------------------------------------------
# maybe_warm_start dimension guard
#
# These are the tests whose absence let the brain die for 64 nights. This
# file was named "warmstart" but only ever covered the two ordering helpers
# below it -- it never fed maybe_warm_start a checkpoint at all, let alone a
# dimension-mismatched one, so it could not fail for the reason it existed.
# Each test below was mutation-checked by reverting the guard and confirming
# it goes red.
# ---------------------------------------------------------------------------


def _save_ckpt(path: Path, *, obs_dim: int) -> None:
    """Write a checkpoint in train_brain's production format at `obs_dim`."""
    import torch

    from app.rl.policy import PolicyNetwork

    torch.manual_seed(7)
    policy = PolicyNetwork(obs_dim=obs_dim)
    table = AssetEmbeddingTable()
    table.bulk_register(["BTCUSDT", "ETHUSDT"])
    torch.save(
        {"policy": policy.state_dict(), "asset_table": table.state_dict()}, path,
    )


def _live_dim() -> int:
    from app.rl.policy import PolicyNetwork

    return int(PolicyNetwork().shared[0].weight.shape[1])


def test_warm_start_rejects_obs_dim_mismatch_and_cold_starts(tmp_path: Path) -> None:
    """The 2026-08-17 regression: a checkpoint one dim wider than the live
    policy must COLD START, not raise. Reproduces OBS_DIM 54 -> 53."""
    from app.rl.policy import PolicyNetwork

    ckpt = tmp_path / "stale.pt"
    _save_ckpt(ckpt, obs_dim=_live_dim() + 1)

    policy = PolicyNetwork()
    table = AssetEmbeddingTable()
    msgs: list[str] = []

    warm = train_brain.maybe_warm_start(policy, table, ckpt, on_incompatible=msgs.append)

    assert warm is False, "a mismatched checkpoint must not report a warm start"
    assert len(msgs) == 1, "an incompatible checkpoint must be reported to the caller"
    assert "OBS_DIM" in msgs[0]
    assert "COLD START" in msgs[0].upper()


def test_warm_start_mismatch_leaves_policy_untouched(tmp_path: Path) -> None:
    """A rejected warm-start must not partially overwrite the random init."""
    import torch

    from app.rl.policy import PolicyNetwork

    ckpt = tmp_path / "stale.pt"
    _save_ckpt(ckpt, obs_dim=_live_dim() + 1)

    torch.manual_seed(123)
    policy = PolicyNetwork()
    before = {k: v.clone() for k, v in policy.state_dict().items()}

    train_brain.maybe_warm_start(policy, AssetEmbeddingTable(), ckpt)

    after = policy.state_dict()
    for key, tensor in before.items():
        assert torch.equal(tensor, after[key]), f"{key} was mutated by a rejected load"


def test_warm_start_accepts_matching_obs_dim(tmp_path: Path) -> None:
    """Positive control: the guard must not block a compatible checkpoint.

    Without this, a guard that rejected EVERYTHING would pass the tests
    above while leaving the brain just as dead.
    """
    import torch

    from app.rl.policy import PolicyNetwork

    ckpt = tmp_path / "good.pt"
    _save_ckpt(ckpt, obs_dim=_live_dim())

    policy = PolicyNetwork()
    table = AssetEmbeddingTable()
    msgs: list[str] = []

    warm = train_brain.maybe_warm_start(policy, table, ckpt, on_incompatible=msgs.append)

    assert warm is True, "a same-dim checkpoint must warm-start"
    assert msgs == [], "a compatible checkpoint must not be reported incompatible"
    # And the weights actually came from the file, not the random init.
    saved = torch.load(ckpt, map_location="cpu", weights_only=False)
    assert torch.equal(
        policy.state_dict()["shared.0.weight"], saved["policy"]["shared.0.weight"],
    )


def test_missing_checkpoint_is_not_reported_as_incompatible(tmp_path: Path) -> None:
    """The two causes of False are different events and must stay distinct:
    a first-ever run is routine, an incompatible checkpoint is an alert."""
    from app.rl.policy import PolicyNetwork

    msgs: list[str] = []
    warm = train_brain.maybe_warm_start(
        PolicyNetwork(), AssetEmbeddingTable(),
        tmp_path / "does_not_exist.pt", on_incompatible=msgs.append,
    )

    assert warm is False
    assert msgs == [], "a missing checkpoint must not raise a critical alert"


def test_warm_start_survives_a_corrupt_checkpoint(tmp_path: Path) -> None:
    """Non-dim incompatibilities (truncated/garbage payload) also cold-start."""
    import torch

    from app.rl.policy import PolicyNetwork

    ckpt = tmp_path / "corrupt.pt"
    torch.save({"policy": {"shared.0.weight": "not a tensor"}, "asset_table": {}}, ckpt)

    msgs: list[str] = []
    warm = train_brain.maybe_warm_start(
        PolicyNetwork(), AssetEmbeddingTable(), ckpt, on_incompatible=msgs.append,
    )

    assert warm is False
    assert len(msgs) == 1


def test_checkpoint_obs_dim_reads_the_width() -> None:
    """Helper reads the real width and returns None rather than guessing."""
    import torch

    from app.rl.policy import PolicyNetwork

    state = {"policy": PolicyNetwork(obs_dim=41).state_dict()}
    assert train_brain.checkpoint_obs_dim(state) == 41

    assert train_brain.checkpoint_obs_dim({"policy": {}}) is None
    assert train_brain.checkpoint_obs_dim({}) is None
    assert train_brain.checkpoint_obs_dim(None) is None
    assert train_brain.checkpoint_obs_dim({"policy": {"shared.0.weight": 5}}) is None
    _ = torch
