import pytest


@pytest.fixture(autouse=True)
def _reset_settings_singleton():
    """Reset cached settings so env patches take effect in every test."""
    import data_analyst.config.settings as m
    m._settings = None
    yield
    m._settings = None
