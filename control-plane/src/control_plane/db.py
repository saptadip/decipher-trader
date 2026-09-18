from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from control_plane.config import get_settings


def _configure_pragmas(engine: Engine, *, wal: bool) -> None:
    journal = "WAL" if wal else "DELETE"

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA journal_mode={journal};")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


def get_engine(db_path: Path | None = None) -> Engine:
    settings = get_settings()
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # NullPool avoids connection reuse; each request opens and closes its own
    # SQLite connection, preventing stale reads when the DB is written by an
    # external process (e.g. the E2E smoke-test backdating paper_started_at).
    engine = create_engine(f"sqlite:///{path}", future=True, poolclass=NullPool)
    _configure_pragmas(engine, wal=settings.sqlite_wal)
    return engine


def get_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def get_session():
    global _engine, _Session
    if _engine is None:
        _engine = get_engine()
        _Session = get_sessionmaker(_engine)
    assert _Session is not None
    with _Session() as session:
        yield session
