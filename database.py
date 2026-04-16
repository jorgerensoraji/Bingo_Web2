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
        "ALTER TABLE sessions ADD COLUMN premio_fijo FLOAT DEFAULT 0.0",
    ]
    with engine.connect() as conn:
        for sql in new_columns:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists — ignore
    _backfill_winners_table()

def _backfill_winners_table():
    """Copy existing winners from sessions.winners_final JSON into the winners table."""
    import json
    from datetime import datetime
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT id, bingo_nombre, date, winners_final FROM sessions WHERE winners_final IS NOT NULL AND winners_final != '[]'")).fetchall()
        for row in rows:
            sid, bingo_nombre, date, wf_json = row
            try:
                wf = json.loads(wf_json or "[]")
            except Exception:
                continue
            for w in wf:
                cid  = w.get("id", "") or w.get("cartilla_id", "")
                tipo = w.get("tipo", "bingo")
                if not cid or not sid:
                    continue
                # Determine prize amount based on tipo
                if tipo == "linea":
                    amt = float(w.get("linea_prize", 0) or 0)
                elif tipo == "u":
                    amt = float(w.get("u_prize", 0) or 0)
                elif tipo == "o":
                    amt = float(w.get("o_prize", 0) or 0)
                else:
                    amt = float(w.get("prize", 0) or 0)
                existing = db.execute(
                    text("SELECT id FROM winners WHERE session_id=:sid AND cartilla_id=:cid AND tipo=:tipo"),
                    {"sid": sid, "cid": cid, "tipo": tipo}
                ).fetchone()
                if existing:
                    continue
                db.execute(text("""
                    INSERT INTO winners
                    (session_id, cartilla_id, tipo, nombre, apellidos, email, celular, yape_plin,
                     prize_amount, drawn_count, claimed_at, game_id, n_winners, split,
                     merged_o, merged_u, linea_row, puede_reclamar_bingo,
                     paid, paid_at, paid_method, paid_ref, paid_note, created)
                    VALUES
                    (:sid, :cid, :tipo, :nombre, :apellidos, :email, :celular, :yape_plin,
                     :prize_amount, :drawn_count, :claimed_at, :game_id, :n_winners, :split,
                     :merged_o, :merged_u, :linea_row, :puede_reclamar_bingo,
                     :paid, :paid_at, :paid_method, :paid_ref, :paid_note, :created)
                """), {
                    "sid":          sid,
                    "cid":          cid,
                    "tipo":         tipo,
                    "nombre":       w.get("nombre", ""),
                    "apellidos":    w.get("apellidos", ""),
                    "email":        w.get("email", ""),
                    "celular":      w.get("celular", ""),
                    "yape_plin":    w.get("yape_plin", ""),
                    "prize_amount": amt,
                    "drawn_count":  int(w.get("drawn_count", 0) or 0),
                    "claimed_at":   w.get("claimed_at", ""),
                    "game_id":      w.get("game_id", ""),
                    "n_winners":    int(w.get("n_winners", 1) or 1),
                    "split":        1 if w.get("split") else 0,
                    "merged_o":     float(w.get("merged_o", 0) or 0),
                    "merged_u":     float(w.get("merged_u", 0) or 0),
                    "linea_row":    w.get("linea_row"),
                    "puede_reclamar_bingo": 1 if w.get("puede_reclamar_bingo") else 0,
                    "paid":         1 if w.get("paid") else 0,
                    "paid_at":      w.get("paid_at", ""),
                    "paid_method":  w.get("paid_method", ""),
                    "paid_ref":     w.get("paid_ref", ""),
                    "paid_note":    w.get("paid_note", ""),
                    "created":      w.get("claimed_at") or date or datetime.now().isoformat(timespec="seconds"),
                })
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[WARN] _backfill_winners_table: {e}")
    finally:
        db.close()

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