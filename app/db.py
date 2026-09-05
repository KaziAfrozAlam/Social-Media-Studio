from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import resolve_db_path, Settings


def build_engine(database_url: str):
    if database_url.startswith("sqlite:///"):
        Path("data").mkdir(parents=True, exist_ok=True)
        resolve_db_path(database_url).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url)


_engine = None
_SessionLocal = None

Base = declarative_base()


def reset_db():
    """For tests that re-point DATABASE_URL between imports."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


def get_engine(settings: Settings):
    global _engine
    if _engine is None:
        _engine = build_engine(settings.database_url)
    return _engine


def get_session_factory(settings: Settings):
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(settings), expire_on_commit=False)
    return _SessionLocal


def init_db(settings: Settings):
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine(settings))


def session_scope(settings: Settings):
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        Session = get_session_factory(settings)
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _scope()