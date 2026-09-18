from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from control_plane.config import get_settings


def _enable_wal(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


def get_engine(db_path: Path | None = None) -> Engine:
    settings = get_settings()
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", future=True)
    _enable_wal(engine)
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
