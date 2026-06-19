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
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(conversation_sessions)"))}
        if "name" not in existing:
            conn.execute(text("ALTER TABLE conversation_sessions ADD COLUMN name TEXT"))
            conn.commit()
