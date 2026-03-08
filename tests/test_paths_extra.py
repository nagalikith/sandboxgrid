import pytest

from sandbox_api.platform.core.paths import normalize_artifacts_mode


def test_normalize_artifacts_mode_invalid():
    with pytest.raises(ValueError):
        normalize_artifacts_mode("bad")
