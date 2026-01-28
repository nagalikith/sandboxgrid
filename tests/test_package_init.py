import pytest

import sandbox_api


def test_package_getattr_app():
    app = sandbox_api.app
    assert app


def test_package_getattr_invalid():
    with pytest.raises(AttributeError):
        _ = sandbox_api.missing
