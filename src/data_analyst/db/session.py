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
            # SQLite ALTER TABLE only allows constant/NULL defaults; datetime('now') is a function
            # SQLAlchemy's Python-side default=_now handles new rows; NULLs are tolerated in _stale()
            ("updated_at", "TIMESTAMP"),
            ("origin", "TEXT DEFAULT 'uploaded'"),
            ("derived_from_run_id", "TEXT"),
            ("derived_from_dataset_ids", "TEXT"),
            ("derivation_code", "TEXT"),
            ("parquet_path", "TEXT"),
            ("auto_notes_status", "TEXT"),   # C30
            ("context_facts", "TEXT"),        # C31
        ]
        for col, definition in _ds_migrations:
            if col not in ds_cols:
                conn.execute(text(f"ALTER TABLE datasets ADD COLUMN {col} {definition}"))
        conn.commit()

        run_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(query_runs)"))}
        _run_migrations = [
            ("prompt_breakdown", "TEXT"),     # C29
            ("dataset_ids_json", "TEXT"),     # C14 multi-dataset (alembic 961cff3c246b)
            ("selector_reasoning", "TEXT"),   # C19 selector (alembic 3ee8b16c70eb)
        ]
        for col, definition in _run_migrations:
            if col not in run_cols:
                conn.execute(text(f"ALTER TABLE query_runs ADD COLUMN {col} {definition}"))
        conn.commit()
