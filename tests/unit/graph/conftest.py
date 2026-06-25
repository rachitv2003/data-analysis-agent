import pytest


@pytest.fixture
def _tmp_db(tmp_path, monkeypatch):
    """Point the DB at a throwaway file and reset the cached engine/settings so
    graph helpers that open their own sessions hit the temp DB, not the repo's
    default sqlite file."""
    monkeypatch.setenv("DATA_ANALYST_DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    import data_analyst.config.settings as settings_module
    from data_analyst.db import session as session_module
    monkeypatch.setattr(settings_module, "_settings", None)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_SessionLocal", None)
    yield
