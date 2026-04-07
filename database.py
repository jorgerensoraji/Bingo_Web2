"""
database.py — Bingo Pro v8.0
SQLite connection with WAL journal mode for safe concurrent access.
"""

import os
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).parent
DB_PATH  = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR}/bingo.db")

# ── Engine ─────────────────────────────────────────────────────────────────────
# check_same_thread=False is required for Flask multi-threaded mode.
# WAL mode allows concurrent reads + writes without full-table locks.
engine = create_engine(
    DB_PATH,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    """Enable WAL mode and foreign keys on every new connection."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")   # safe concurrent writes
    cur.execute("PRAGMA synchronous=NORMAL") # faster than FULL, still safe
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")  # wait 5s before 'database is locked'
    cur.close()

# ── Session factory ────────────────────────────────────────────────────────────
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base         = declarative_base()

def init_db():
    """Create all tables and run lightweight migrations (safe to call on every startup)."""
    import models  # noqa: F401 — registers models with Base
    Base.metadata.create_all(bind=engine)
    _migrate()

def _migrate():
    """Add new columns to existing tables if they don't exist (SQLite ALTER TABLE)."""
    new_columns = [
        "ALTER TABLE vouchers ADD COLUMN yape_plin VARCHAR(20) DEFAULT ''",
        "ALTER TABLE vouchers ADD COLUMN terms_accepted BOOLEAN DEFAULT 0",
        "ALTER TABLE vouchers ADD COLUMN access_pin VARCHAR(64) DEFAULT ''",
        "ALTER TABLE vouchers ADD COLUMN pin_hint VARCHAR(4) DEFAULT ''",
    ]
    with engine.connect() as conn:
        for sql in new_columns:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists — ignore

def get_db():
    """
    Flask request-scoped session.
    Use as:
        db = get_db()
        ...
        db.close()
    Or via the teardown_appcontext hook in app.py.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()