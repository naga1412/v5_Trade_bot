import pytest


@pytest.fixture
def empty_prev_hash() -> str:
    return "0" * 64
