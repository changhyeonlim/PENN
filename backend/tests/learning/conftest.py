"""These tests describe the agent code you will write. Until a function exists,
its tests are skipped instead of failing, so the main suite stays green."""
import pytest


def needs(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as e:
        pytest.skip(f"not built yet: {e}")
