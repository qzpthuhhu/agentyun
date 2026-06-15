"""Database setup."""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .config import settings

# Connect args for SQLite
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=settings.debug,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Session:
    """FastAPI dependency for DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Used at startup for SQLite dev mode.

    For v0.3+ we also run lightweight additive migrations: any new column
    we add to models gets ALTER TABLE'd in. We never drop columns or change
    types here — that's alembic territory.
    """
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()


def _run_lightweight_migrations():
    """Add new columns to existing tables if missing (best-effort).

    For SQLite, use ALTER TABLE ADD COLUMN. Errors are silently ignored
    (column already exists).
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)

    # v0.3: shares.label
    if insp.has_table("shares") and not _column_exists(insp, "shares", "label"):
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE shares ADD COLUMN label VARCHAR"))
        except Exception:
            pass


def _column_exists(insp, table: str, column: str) -> bool:
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
        return column in cols
    except Exception:
        return False