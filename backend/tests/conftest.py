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


@pytest.fixture
def empty_prev_hash() -> str:
    return "0" * 64
