#!/usr/bin/env python3
"""
migrate_to_sqlite.py — Bingo Pro v8.0
Migrates all existing JSON data → SQLite database.

Run ONCE before starting the new app:
    python migrate_to_sqlite.py

Safe to run multiple times (skips already-migrated records).
"""

import json, sys
from pathlib import Path

# ── Locate project ────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "cartillas_data"
DB_PATH       = BASE_DIR / "bingo.db"

if not DATA_DIR.exists():
    print("✅ No cartillas_data/ found — nothing to migrate. Fresh start!")
    sys.exit(0)

# ── Bootstrap the new DB schema first ─────────────────────────────────────────
# Import the new app's db module just for init
try:
    from database import init_db, SessionLocal
    from models import Voucher, Session as BingoSession, Cartilla, Config
except ImportError:
    print("❌ Could not import database/models. Make sure database.py and models.py exist.")
    sys.exit(1)

init_db()
db = SessionLocal()

migrated = {"vouchers": 0, "sessions": 0, "cartillas": 0, "config": 0}
skipped  = {"vouchers": 0, "sessions": 0, "cartillas": 0}

# ── 1. Migrate config ─────────────────────────────────────────────────────────
cfg_file = DATA_DIR / "_config.json"
if cfg_file.exists():
    try:
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        existing = db.query(Config).filter_by(key="main").first()
        if not existing:
            db.add(Config(key="main", value=json.dumps(raw, ensure_ascii=False)))
            db.commit()
            migrated["config"] += 1
            print("  ✅ Config migrated")
        else:
            print("  ⏭  Config already exists — skipped")
    except Exception as e:
        print(f"  ⚠️  Config migration failed: {e}")

# ── 2. Migrate sessions ───────────────────────────────────────────────────────
sess_file = DATA_DIR / "_sessions.json"
if sess_file.exists():
    try:
        sessions = json.loads(sess_file.read_text(encoding="utf-8"))
        for s in sessions:
            sid = s.get("id", "")
            if not sid:
                continue
            existing = db.query(BingoSession).filter_by(id=sid).first()
            if existing:
                skipped["sessions"] += 1
                continue
            row = BingoSession(
                id           = sid,
                bingo_type   = s.get("bingo_type", "1sol"),
                bingo_nombre = s.get("bingo_nombre", ""),
                bingo_color  = s.get("bingo_color", ""),
                bingo_precio = float(s.get("bingo_precio", 0)),
                datetime_iso = s.get("datetime_iso", ""),
                date         = s.get("date", ""),
                time         = s.get("time", ""),
                descripcion  = s.get("descripcion", ""),
                max_players  = int(s.get("max_players", 0)),
                status       = s.get("status", "scheduled"),
                created      = s.get("created", ""),
                started_at   = s.get("started_at"),
                finished_at  = s.get("finished_at"),
                prepare_at   = s.get("prepare_at"),
                prepare_secs = int(s.get("prepare_secs", 60)),
                prize_info   = json.dumps(s.get("prize_info") or {}, ensure_ascii=False),
                winners_final= json.dumps(s.get("winners_final") or [], ensure_ascii=False),
            )
            db.add(row)
            migrated["sessions"] += 1
        db.commit()
        print(f"  ✅ Sessions: {migrated['sessions']} migrated, {skipped['sessions']} skipped")
    except Exception as e:
        db.rollback()
        print(f"  ⚠️  Sessions migration failed: {e}")

# ── 3. Migrate vouchers ───────────────────────────────────────────────────────
v_file = DATA_DIR / "_vouchers.json"
if v_file.exists():
    try:
        vouchers = json.loads(v_file.read_text(encoding="utf-8"))
        for v in vouchers:
            code = (v.get("code") or "").strip().upper()
            if not code:
                continue
            existing = db.query(Voucher).filter_by(code=code).first()
            if existing:
                skipped["vouchers"] += 1
                continue
            row = Voucher(
                code                  = code,
                nombres               = v.get("nombres", ""),
                apellidos             = v.get("apellidos", ""),
                email                 = v.get("email", ""),
                celular               = v.get("celular", ""),
                bingo_type            = v.get("bingo_type", "1sol"),
                bingo_nombre          = v.get("bingo_nombre", ""),
                precio                = float(v.get("precio", 0)),
                session_id            = v.get("session_id", ""),
                payment_method        = v.get("payment_method", ""),
                payment_ref           = v.get("payment_ref", ""),
                payment_status        = v.get("payment_status", "pending"),
                payment_submitted_at  = v.get("payment_submitted_at"),
                approved_at           = v.get("approved_at"),
                approved_note         = v.get("approved_note", ""),
                rejected_at           = v.get("rejected_at"),
                rejected_reason       = v.get("rejected_reason", ""),
                email_codigo_enviado  = bool(v.get("email_codigo_enviado", False)),
                email_enviado_at      = v.get("email_enviado_at"),
                creado_por            = v.get("creado_por", "admin"),
                cartillas_ids         = json.dumps(v.get("cartillas", []), ensure_ascii=False),
                created               = v.get("created", ""),
            )
            db.add(row)
            migrated["vouchers"] += 1
        db.commit()
        print(f"  ✅ Vouchers: {migrated['vouchers']} migrated, {skipped['vouchers']} skipped")
    except Exception as e:
        db.rollback()
        print(f"  ⚠️  Vouchers migration failed: {e}")

# ── 4. Migrate cartillas (individual JSON files) ──────────────────────────────
cart_count = 0
skip_count = 0
for f in sorted(DATA_DIR.glob("*.json")):
    if f.name.startswith("_"):
        continue
    try:
        c = json.loads(f.read_text(encoding="utf-8"))
        cid = c.get("id", "")
        if not cid:
            continue
        existing = db.query(Cartilla).filter_by(id=cid).first()
        if existing:
            skip_count += 1
            continue
        row = Cartilla(
            id                = cid,
            nombre            = c.get("nombre", ""),
            telefono          = c.get("telefono", ""),
            voucher_code      = c.get("voucher_code", ""),
            session_id        = c.get("session_id", ""),
            bingo_type        = c.get("bingo_type", "1sol"),
            grid              = json.dumps(c.get("grid", []), ensure_ascii=False),
            created           = c.get("created", ""),
            generada_por_admin= bool(c.get("generada_por_admin", False)),
        )
        db.add(row)
        cart_count += 1
        if cart_count % 100 == 0:
            db.commit()
            print(f"  ... {cart_count} cartillas migrated so far")
    except Exception as e:
        print(f"  ⚠️  Cartilla {f.name} failed: {e}")

db.commit()
db.close()
print(f"  ✅ Cartillas: {cart_count} migrated, {skip_count} skipped")

print()
print("═" * 50)
print("  Migration complete!")
print(f"  DB: {DB_PATH}")
print("═" * 50)
print()
print("Next step: python app.py")