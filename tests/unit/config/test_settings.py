import pytest
from data_analyst.config.settings import get_settings


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("DATA_ANALYST_DATABASE_URL", "sqlite:///test.db")
    monkeypatch.delenv("DATA_ANALYST_GEMINI_API_KEY", raising=False)
    s = get_settings()
    assert s.database_url == "sqlite:///test.db"
    assert s.gemini_api_key == ""
    assert s.llm_model == "gemini-2.5-flash"
    assert s.max_iterations == 10


def test_settings_override(monkeypatch):
    monkeypatch.setenv("DATA_ANALYST_DATABASE_URL", "sqlite:///custom.db")
    monkeypatch.setenv("DATA_ANALYST_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DATA_ANALYST_MAX_ITERATIONS", "5")
    s = get_settings()
    assert s.database_url == "sqlite:///custom.db"
    assert s.gemini_api_key == "test-key"
    assert s.max_iterations == 5
