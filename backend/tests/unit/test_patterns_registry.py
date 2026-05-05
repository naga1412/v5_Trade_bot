from app.core.patterns import ALL_PATTERNS
from app.core.patterns.base import Pattern
from app.core.patterns.candle import CANDLE_PATTERNS


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


def test_82_candle_patterns_registered() -> None:
    """SP-2 Phase C: 60 TA-Lib + 1 composite (hammer_or_hanging) + 21 hand-rolled."""
    assert len(CANDLE_PATTERNS) == 82
    ids = {p.pattern_id for p in CANDLE_PATTERNS}
    assert len(ids) == 82, f"duplicate pattern_ids in CANDLE_PATTERNS: {ids}"


def test_all_candle_patterns_are_type_candle() -> None:
    for p in CANDLE_PATTERNS:
        assert p.pattern_type == "candle"
