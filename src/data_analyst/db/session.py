from contextlib import contextmanager
from collections.abc import Generator

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        from data_analyst.config.settings import get_settings
        _engine = create_engine(get_settings().database_url, echo=False)
    return _engine


def _get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    with _get_session_factory()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@contextmanager
def create_db_session() -> Generator[Session, None, None]:
    """Standalone — for graph nodes, CLI, scripts."""
    with _get_session_factory()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def init_db() -> None:
    from data_analyst.db.models import Base
    from sqlalchemy import text
    engine = _get_engine()
    Base.metadata.create_all(bind=engine)
    # Incremental migrations — add columns that create_all won't add to existing tables
    with engine.connect() as conn:
        sess_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(conversation_sessions)"))}
        _sess_migrations = [
            ("name", "TEXT"),
            ("dataset_ids_json", "TEXT"),
        ]
        for col, definition in _sess_migrations:
            if col not in sess_cols:
                conn.execute(text(f"ALTER TABLE conversation_sessions ADD COLUMN {col} {definition}"))
        conn.commit()

        ds_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(datasets)"))}
        _ds_migrations = [
            ("updated_at", "TIMESTAMP DEFAULT (datetime('now'))"),
            ("origin", "TEXT NOT NULL DEFAULT 'uploaded'"),
            ("derived_from_run_id", "TEXT"),
            ("derived_from_dataset_ids", "TEXT"),
            ("derivation_code", "TEXT"),
            ("parquet_path", "TEXT"),
        ]
        for col, definition in _ds_migrations:
            if col not in ds_cols:
                conn.execute(text(f"ALTER TABLE datasets ADD COLUMN {col} {definition}"))
        conn.commit()
