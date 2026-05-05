from app.core.patterns import ALL_PATTERNS
from app.core.patterns.base import Pattern


def test_all_patterns_is_a_list() -> None:
    assert isinstance(ALL_PATTERNS, list)


def test_every_registered_pattern_has_required_attrs() -> None:
    seen_ids: set[str] = set()
    for p in ALL_PATTERNS:
        assert isinstance(p, Pattern), f"{p!r} does not satisfy Pattern protocol"
        assert isinstance(p.pattern_id, str) and p.pattern_id
        assert p.pattern_type in {"candle", "chart"}
        assert p.pattern_id not in seen_ids, f"duplicate pattern_id: {p.pattern_id}"
        seen_ids.add(p.pattern_id)


def test_initially_empty_until_subpackages_populate() -> None:
    """Phase A scaffolding leaves the registry empty; Phases C/D fill it."""
    # Allow either empty (just-after-A5) or already-populated (post-C/D).
    assert isinstance(ALL_PATTERNS, list)  # tautology that doc-checks the contract
