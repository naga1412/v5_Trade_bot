import os

import freezegun
import pytest

# freezegun introspects every loaded module's attributes via getattr() when a
# `freeze_time(...)` block enters, which forces transformers' lazy loader to
# import every model class. Several vision models (mllama, detr, ...) chain
# into `transformers.image_utils` for symbols that only exist when optional
# vision deps are installed (PIL, torchvision); without those, the imports
# raise and the whole `with freeze_time(...):` block fails.
#
# Configure freezegun's default ignore list once here — this applies to every
# freeze_time() call in the suite, including ones in test files that don't
# import this conftest directly.
freezegun.configure(default_ignore_list=[
    "transformers",
    "torch",
    "torchvision",
    "PIL",
    "tensorflow",
])


# PR2: `app.config.Settings` has required fields (database_url, redis_url)
# without defaults. CI seeds them via .github/workflows/ci.yml env blocks,
# but bare `pytest backend/tests` from a fresh shell fails at any import
# path that lru-caches `get_settings()`. Pre-seed in-memory defaults at
# conftest import time so the test suite is usable without
# `DATABASE_URL=... REDIS_URL=...` boilerplate every invocation. CI's
# explicit env wins because `setdefault` is a no-op when the var is set.
os.environ.setdefault(
    "DATABASE_URL", "sqlite+aiosqlite:///:memory:",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def empty_prev_hash() -> str:
    return "0" * 64


@pytest.fixture(autouse=True)
def _pause_state_clean(monkeypatch: pytest.MonkeyPatch):
    """Ensure each test starts with a fresh fakeredis-backed pause_state."""
    import fakeredis.aioredis

    from app.ops import pause_state

    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield
    pause_state._reset_for_tests()
