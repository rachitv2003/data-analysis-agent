"""Regression tests for init_db()'s incremental _run_migrations.

Fresh installs get every column from Base.metadata.create_all(). The risk is an
*upgraded* SQLite DB whose tables predate newer model columns: create_all does
NOT alter existing tables, so the ALTER TABLE statements in _run_migrations are
the only path that backfills them. These tests simulate an old-schema table and
assert init_db() heals it.
"""
import pytest
from sqlalchemy import create_engine, text

from data_analyst.db import session as session_module
import data_analyst.config.settings as settings_module


# Minimal pre-C14/C19 query_runs schema — deliberately omits prompt_breakdown,
# dataset_ids_json, and selector_reasoning so the migration path must add them.
_OLD_QUERY_RUNS = """
CREATE TABLE query_runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    session_id TEXT,
    question TEXT NOT NULL,
    answer TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    action_history TEXT,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    tokens_input INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
"""


@pytest.fixture
def upgraded_db(tmp_path, monkeypatch):
    """An on-disk SQLite DB with an old-schema query_runs table already present,
    wired so init_db() targets it via a fresh engine."""
    db_path = tmp_path / "upgraded.db"
    url = f"sqlite:///{db_path}"

    # Seed the old-schema table before init_db runs.
    seed_engine = create_engine(url)
    with seed_engine.connect() as conn:
        conn.execute(text(_OLD_QUERY_RUNS))
        conn.commit()
    seed_engine.dispose()

    # Point settings + the module-level engine cache at this DB.
    monkeypatch.setenv("DATA_ANALYST_DATABASE_URL", url)
    monkeypatch.setattr(settings_module, "_settings", None)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_SessionLocal", None)
    yield url
    engine = session_module._engine
    if engine is not None:
        engine.dispose()


def _columns(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
    finally:
        engine.dispose()


def test_init_db_backfills_missing_query_runs_columns(upgraded_db):
    """init_db() must ALTER an existing query_runs table to add the C14/C19/C29
    columns that create_all() can't add to a pre-existing table."""
    before = _columns(upgraded_db, "query_runs")
    assert "dataset_ids_json" not in before
    assert "selector_reasoning" not in before
    assert "prompt_breakdown" not in before

    session_module.init_db()

    after = _columns(upgraded_db, "query_runs")
    assert "dataset_ids_json" in after   # C14 multi-dataset
    assert "selector_reasoning" in after  # C19 selector
    assert "prompt_breakdown" in after    # C29 token breakdown


def test_init_db_is_idempotent_on_query_runs(upgraded_db):
    """Running init_db() twice must not fail on already-added columns."""
    session_module.init_db()
    # Second call should be a no-op for the run-table migrations, not a
    # duplicate-column error.
    session_module.init_db()
    after = _columns(upgraded_db, "query_runs")
    assert {"dataset_ids_json", "selector_reasoning", "prompt_breakdown"} <= after
