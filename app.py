#!/usr/bin/env python3
"""
BINGO PRO WEB v8.0 — app.py
Made by Renso Ramirez  |  SQLite migration by Claude

CHANGES v8.0 (SQLite migration):
  ✅ All JSON file storage replaced with SQLite + SQLAlchemy
  ✅ WAL journal mode — safe concurrent reads/writes
  ✅ Thread-safe DB sessions replacing manual threading.Lock()
  ✅ 100% API-compatible with v7.2 — no frontend changes needed
  ✅ migrate_to_sqlite.py imports existing data automatically
  ✅ Foundation ready for APScheduler auto-draw (v8.1)
  ✅ Foundation ready for payment webhooks (v8.2)
"""

import asyncio, json, os, random, socket, tempfile
import threading, time, uuid
from security import (
    apply_security_headers, get_csrf_token, csrf_required,
    check_ip_allowed, record_failed_login, record_success_login,
    check_honeypot, sanitize_text, sanitize_email, sanitize_phone,
    get_real_ip, get_security_stats,
    totp_verify, totp_new_secret, totp_provisioning_uri,
)
try:
    import autodraw
except ModuleNotFoundError:
    class _AutoDrawStub:
        def init(self, **kwargs):
            return None

        def start_scheduler(self):
            return None

        def configure_session(self, *args, **kwargs):
            return None

        def stop_session(self, *args, **kwargs):
            return None

        def get_status(self):
            return {"enabled": False, "reason": "autodraw module not installed"}

        def get_audit_log(self, limit=50):
            return []

    autodraw = _AutoDrawStub()
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from io import BytesIO
from pathlib import Path

import requests as http_requests
import edge_tts
from flask import Flask, jsonify, render_template, request, send_file, session, redirect
from num2words import num2words
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from PIL import Image, ImageDraw, ImageFont
import qrcode



from database import init_db, SessionLocal
from models import Voucher, Session as BingoSession, Cartilla, Config

# ── Cargar .env ───────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.after_request(apply_security_headers)

# ─── Seguridad ────────────────────────────────────────────────────────────────
_raw_key = os.environ.get("SECRET_KEY", "")
if not _raw_key:
    import secrets as _sec
    _raw_key = _sec.token_hex(32)
    print("\nADVERTENCIA: SECRET_KEY no configurada - usando clave temporal.\n")

app.secret_key = _raw_key
TOTP_SECRET        = os.environ.get("TOTP_SECRET", "")
AUTO_DRAW_INTERVAL = int(os.environ.get("AUTO_DRAW_INTERVAL", "15"))
AUTO_DRAW_VOICE    = os.environ.get("AUTO_DRAW_VOICE", "es-PE-CamilaNeural")
ADMIN_USER = os.environ.get("ADMIN_USER", "").strip()
ADMIN_PASS = os.environ.get("ADMIN_PASS", "").strip()

if not ADMIN_USER or not ADMIN_PASS:
    print("\nERROR: ADMIN_USER y ADMIN_PASS no configurados.\n")

app.config["SESSION_PERMANENT"]       = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

def is_admin() -> bool:
    return bool(session.get("is_admin"))

def admin_required():
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401
    return None

# ─── DB session helper ────────────────────────────────────────────────────────
@contextmanager
def db_session():
    """Context manager — always commits or rolls back, always closes."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ─── Configuración de Email (Brevo HTTP API) ──────────────────────────────────
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "")
EMAIL_PASS    = os.environ.get("EMAIL_PASS", "")   # Brevo SMTP key = API key
EMAIL_NOMBRE  = os.environ.get("EMAIL_NOMBRE", "Bingo Pro")
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

def email_configurado() -> bool:
    return bool(EMAIL_FROM and EMAIL_PASS)

def enviar_email(destinatario: str, asunto: str, cuerpo_html: str) -> tuple:
    if not email_configurado():
        return False, "Email no configurado. Agrega EMAIL_FROM y EMAIL_PASS en las variables de entorno."
    if not destinatario or "@" not in destinatario:
        return False, "Email del destinatario inválido"
    try:
        payload = {
            "sender":      {"name": EMAIL_NOMBRE, "email": EMAIL_FROM},
            "to":          [{"email": destinatario}],
            "subject":     asunto,
            "htmlContent": cuerpo_html,
        }
        headers = {
            "api-key":      EMAIL_PASS,
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }
        # Forzar IPv4 para evitar "Network unreachable" en servidores sin IPv6
        session = http_requests.Session()
        session.mount("https://", http_requests.adapters.HTTPAdapter())
        old_getaddrinfo = socket.getaddrinfo
        def _ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
            return old_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
        socket.getaddrinfo = _ipv4_getaddrinfo
        try:
            resp = session.post(BREVO_API_URL, json=payload, headers=headers, timeout=20)
        finally:
            socket.getaddrinfo = old_getaddrinfo

        if resp.status_code in (200, 201):
            return True, ""
        try:
            err_body = resp.json()
            err_msg  = err_body.get("message", resp.text[:200])
        except Exception:
            err_msg = resp.text[:200]
        return False, f"Brevo API error {resp.status_code}: {err_msg}"
    except http_requests.exceptions.Timeout:
        return False, "Timeout al conectar con Brevo API."
    except Exception as e:
        return False, f"Error al enviar email: {e}"

# ─── Email templates (unchanged from v7.2) ───────────────────────────────────
def _email_codigo_voucher(voucher_dict: dict, url_base: str) -> str:
    bt     = BINGO_TYPES.get(voucher_dict.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    code   = voucher_dict.get("code", "")
    nombre = voucher_dict.get("nombres", "Jugador")
    precio = bt["precio"]
    url_cartillas = f"{url_base}/cartillas"
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:28px">
    <h1 style="font-size:2rem;color:#00e5b4;letter-spacing:3px;margin:0">🎱 BINGO PRO</h1>
    <p style="color:#4a6b85;margin:4px 0 0">Tu código de participación</p>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:14px;padding:24px;margin-bottom:20px">
    <p style="margin:0 0 8px;color:#4a6b85;font-size:.85rem">Hola, <strong style="color:#ddeeff">{nombre}</strong></p>
    <p style="margin:0 0 20px;color:#ddeeff">¡Tu pago fue confirmado! Aquí está tu código:</p>
    <div style="background:#111f2e;border:2px solid #00e5b4;border-radius:12px;padding:20px;text-align:center;margin-bottom:20px">
      <div style="font-size:.75rem;color:#4a6b85;letter-spacing:3px;text-transform:uppercase;margin-bottom:8px">Tu código</div>
      <div style="font-family:'Courier New',monospace;font-size:2.4rem;font-weight:900;color:#00e5b4;letter-spacing:8px">{code}</div>
      <div style="font-size:.78rem;color:#4a6b85;margin-top:8px">{bt['emoji']} {bt['nombre']} — S/. {precio:.2f}</div>
    </div>
    <div style="background:#0a1520;border-radius:10px;padding:14px;font-size:.85rem;color:#4a6b85;margin-bottom:20px">
      <strong style="color:#ddeeff">¿Cómo usar tu código?</strong><br><br>
      1. Entra a <a href="{url_cartillas}" style="color:#00e5b4">{url_cartillas}</a><br>
      2. Ingresa el código <strong style="color:#00e5b4">{code}</strong><br>
      3. Genera tu cartilla y descárgala<br>
      4. ¡Espera el inicio del juego!
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin:0">Bingo Pro Web v8.0 · {EMAIL_NOMBRE}</p>
</div></body></html>"""

def _email_pago_recibido(voucher_dict: dict) -> str:
    bt     = BINGO_TYPES.get(voucher_dict.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    nombre = voucher_dict.get("nombres", "Jugador")
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:28px">
    <h1 style="font-size:2rem;color:#00e5b4;letter-spacing:3px;margin:0">🎱 BINGO PRO</h1>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:14px;padding:24px">
    <p style="margin:0 0 12px">Hola <strong style="color:#ddeeff">{nombre}</strong>,</p>
    <p style="color:#ddeeff;margin:0 0 16px">Recibimos tu comprobante para
      <strong style="color:{bt['color']}">{bt['emoji']} {bt['nombre']}</strong> (S/. {bt['precio']:.2f}).
    </p>
    <div style="background:#111f2e;border-radius:10px;padding:14px;font-size:.85rem;color:#4a6b85">
      Estamos verificando tu pago. En cuanto sea aprobado recibirás otro email con tu código.<br><br>
      <strong style="color:#f6c343">Tiempo estimado: menos de 30 minutos.</strong>
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin-top:20px">Bingo Pro Web v8.0 · {EMAIL_NOMBRE}</p>
</div></body></html>"""

def _email_aviso_inicio(voucher_dict: dict, sesion_dict: dict, url_base: str, segundos: int) -> str:
    bt       = BINGO_TYPES.get(sesion_dict.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    nombre   = voucher_dict.get("nombres", "Jugador")
    dt_str   = sesion_dict.get("datetime_iso", "").replace("T", " ")[:16]
    url_juego= f"{url_base}/"
    mins     = segundos // 60
    tiempo_txt = (f"{mins} minuto{'s' if mins != 1 else ''}" if segundos >= 60 else f"{segundos} segundos")
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <h1 style="font-size:2rem;color:#e74c3c;letter-spacing:2px;margin:0">⏰ ¡EMPIEZA EN {tiempo_txt.upper()}!</h1>
    <p style="color:#4a6b85;margin:6px 0 0">{bt['emoji']} {bt['nombre']} — Bingo Pro</p>
  </div>
  <div style="background:#0d1825;border:2px solid #e74c3c;border-radius:14px;padding:24px;margin-bottom:20px;text-align:center">
    <p style="margin:0 0 10px">Hola <strong style="color:#ddeeff">{nombre}</strong>,</p>
    <p style="color:#ddeeff;font-size:1rem;margin:0 0 20px">¡El sorteo empieza muy pronto! Entra ya y ten tu cartilla lista.</p>
    <a href="{url_juego}" style="display:inline-block;padding:16px 32px;background:#e74c3c;color:white;border-radius:12px;text-decoration:none;font-weight:900;font-size:1.05rem">🎱 ENTRAR AL JUEGO AHORA →</a>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:12px;padding:14px;font-size:.82rem;color:#4a6b85">
    <div style="margin-bottom:6px">📅 Sesión: <strong style="color:#ddeeff">{dt_str}</strong></div>
    <div>🌐 URL: <a href="{url_juego}" style="color:#00e5b4">{url_juego}</a></div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.72rem;margin-top:20px">Bingo Pro Web v8.0 · {EMAIL_NOMBRE}</p>
</div></body></html>"""

def _email_ganador(winner: dict, btype: dict) -> str:
    nombre     = winner.get("nombre", "Jugador")
    prize      = winner.get("prize", 0)
    yape_plin  = winner.get("yape_plin", "")
    drawn      = winner.get("drawn_count", 0)
    split      = winner.get("split", False)
    n_winners  = winner.get("n_winners", 1)
    split_note = f"<p style='color:#f6c343;font-size:.88rem;margin:0 0 12px'>Empate entre {n_winners} ganadores — el pozo se dividio en partes iguales.</p>" if split else ""
    yape_note  = (f"<div style='background:#111f2e;border:2px solid #00e5b4;border-radius:10px;padding:14px;text-align:center;margin:16px 0'>"
                  f"<div style='font-size:.75rem;color:#4a6b85;margin-bottom:4px;letter-spacing:1px;text-transform:uppercase'>Enviamos tu premio a</div>"
                  f"<div style='font-size:1.4rem;font-weight:900;color:#00e5b4;letter-spacing:2px'>{yape_plin}</div>"
                  f"<div style='font-size:.75rem;color:#4a6b85;margin-top:4px'>Yape / Plin registrado al momento de compra</div>"
                  f"</div>"
                  if yape_plin else "")
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <div style="font-size:3rem;margin-bottom:8px">🏆</div>
    <h1 style="font-size:2.2rem;color:#00e5b4;letter-spacing:3px;margin:0">GANASTE</h1>
    <p style="color:#4a6b85;margin:6px 0 0">{btype.get('nombre','Bingo Pro')}</p>
  </div>
  <div style="background:#0d1825;border:2px solid #00e5b4;border-radius:14px;padding:24px">
    <p style="margin:0 0 12px">Felicidades <strong style="color:#ddeeff">{nombre}</strong>!</p>
    {split_note}
    <div style="text-align:center;margin:16px 0">
      <div style="font-size:.8rem;color:#4a6b85;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px">Tu premio</div>
      <div style="font-size:2.8rem;font-weight:900;color:#00e5b4">S/. {prize:.2f}</div>
    </div>
    {yape_note}
    <div style="background:#111f2e;border-radius:10px;padding:12px;font-size:.82rem;color:#4a6b85;margin-top:4px">
      <strong style="color:#ddeeff">Importante:</strong> Si no recibes el pago en los proximos 30 minutos,
      contacta al organizador con tu ID de cartilla: <strong style="color:#f6c343">{winner.get('id','')}</strong>
    </div>
    <div style="font-size:.78rem;color:#4a6b85;margin-top:12px;text-align:center">
      Bolillas sorteadas al momento de ganar: <strong style="color:#ddeeff">{drawn}</strong>
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.72rem;margin-top:20px">Bingo Pro Web v9.0 · {EMAIL_NOMBRE}</p>
</div></body></html>"""

# ─── Rate limiting (unchanged) ────────────────────────────────────────────────
_rate_buckets: dict = defaultdict(list)
_rate_lock = threading.Lock()

def rate_limit(max_calls: int, window_seconds: int):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip  = request.remote_addr or "unknown"
            key = f"{f.__name__}:{ip}"
            now = time.time()
            with _rate_lock:
                calls = _rate_buckets[key]
                calls[:] = [t for t in calls if now - t < window_seconds]
                if len(calls) >= max_calls:
                    return jsonify({"error": "rate_limited", "retry_after": window_seconds}), 429
                calls.append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
TTS_DIR   = Path(tempfile.gettempdir()) / "bingo_web_tts"
TTS_DIR.mkdir(exist_ok=True)

# ─── Tipos de Bingo (unchanged) ───────────────────────────────────────────────
BINGO_TYPES = {
    "1sol": {
        "id": "1sol", "nombre": "Bingo 1 Sol", "precio": 1.00,
        "color": "#f4d03f", "emoji": "🟡", "descripcion": "Entrada básica",
        "prize_pct": 0.70, "linea_pct": 0.10, "max_cartillas_per_voucher": 1,
    },
    "5soles": {
        "id": "5soles", "nombre": "Bingo 5 Soles", "precio": 5.00,
        "color": "#5dade2", "emoji": "🔵", "descripcion": "Entrada estándar",
        "prize_pct": 0.75, "linea_pct": 0.08, "max_cartillas_per_voucher": 1,
    },
    "10soles": {
        "id": "10soles", "nombre": "Bingo 10 Soles", "precio": 10.00,
        "color": "#00e5b4", "emoji": "💎", "descripcion": "Entrada premium",
        "prize_pct": 0.80, "linea_pct": 0.05, "max_cartillas_per_voucher": 1,
    },
}

# ─── DEFAULT CONFIG ───────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "nombre_organizador": "Bingo Pro",
    "whatsapp": "", "facebook": "", "instagram": "", "telefono_extra": "",
    "mensaje_contacto": "¡Hola! Quiero participar en el bingo.",
    "instrucciones_pago": "1. Registra tus datos\n2. Elige tu tipo de bingo\n3. Realiza el pago\n4. Envía número de operación\n5. Espera tu código por email",
    "linea_premio_activo": True,
    "metodos_pago": [
        {"id": "yape",          "activo": True,  "nombre": "Yape",              "emoji": "💜", "numero": "", "titular": "", "instrucciones": "Envía el monto y el número de operación de Yape."},
        {"id": "plin",          "activo": False, "nombre": "Plin",              "emoji": "💙", "numero": "", "titular": "", "instrucciones": ""},
        {"id": "transferencia", "activo": False, "nombre": "Transferencia BCP", "emoji": "🏦", "numero": "", "titular": "", "instrucciones": ""},
        {"id": "interbank",     "activo": False, "nombre": "Interbank",         "emoji": "🏦", "numero": "", "titular": "", "instrucciones": ""},
        {"id": "efectivo",      "activo": True,  "nombre": "Efectivo",          "emoji": "💵", "numero": "", "titular": "", "instrucciones": "Paga presencialmente al organizador."},
    ],
}

# ─── Config helpers ───────────────────────────────────────────────────────────
def _load_config() -> dict:
    with db_session() as db:
        row = db.query(Config).filter_by(key="main").first()
        if not row:
            return dict(DEFAULT_CONFIG)
        try:
            stored = json.loads(row.value)
            cfg    = dict(DEFAULT_CONFIG)
            cfg.update(stored)
            return cfg
        except Exception:
            return dict(DEFAULT_CONFIG)

def _save_config(cfg: dict) -> None:
    with db_session() as db:
        row = db.query(Config).filter_by(key="main").first()
        if row:
            row.value = json.dumps(cfg, ensure_ascii=False)
        else:
            db.add(Config(key="main", value=json.dumps(cfg, ensure_ascii=False)))

# ─── Voucher helpers ──────────────────────────────────────────────────────────
def _gen_voucher_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(6))

def _unique_code(db) -> str:
    for _ in range(50):
        code = _gen_voucher_code()
        if not db.query(Voucher).filter_by(code=code).first():
            return code
    raise RuntimeError("No se pudo generar código único")

def get_voucher_info(code: str) -> dict | None:
    code = (code or "").strip().upper()
    if not code:
        return None
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        return v.to_dict() if v else None

def validate_voucher_for_cartilla(code: str) -> tuple:
    code = (code or "").strip().upper()
    if not code:
        return False, "bad_code"
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        if not v:
            return False, "bad_code"
        if v.payment_status not in ("approved", "manual_approved"):
            return False, "payment_pending"
        # El voucher debe pertenecer a la sesión activa
        with game_lock:
            active_sid = game.session_id
        if active_sid and v.session_id and v.session_id != active_sid:
            return False, "session_mismatch"
        btype = BINGO_TYPES.get(v.bingo_type, BINGO_TYPES["1sol"])
        max_c = btype.get("max_cartillas_per_voucher", 5)
        if len(v.cartillas_list()) >= max_c:
            return False, "max_cartillas_reached"
    return True, ""

def mark_voucher_cartilla(code: str, cartilla_id: str) -> None:
    code = (code or "").strip().upper()
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        if not v:
            return
        ids = v.cartillas_list()
        if cartilla_id not in ids:
            ids.append(cartilla_id)
            v.cartillas_ids = json.dumps(ids, ensure_ascii=False)

def _is_duplicate_payment_ref(ref: str, method: str, session_id: str = "") -> bool:
    if not ref or len(ref.strip()) < 4:
        return False
    ref_norm = ref.strip().upper()
    with db_session() as db:
        q = db.query(Voucher).filter(
            Voucher.payment_method == method,
            Voucher.payment_status.in_(["pending_review", "approved", "manual_approved"])
        )
        for v in q.all():
            if (v.payment_ref or "").strip().upper() == ref_norm:
                if session_id and v.session_id and v.session_id != session_id:
                    continue
                return True
    return False

sessions_lock = threading.Lock()
vouchers_lock = threading.Lock()
CARTILLAS_DIR = BASE_DIR / "cartillas"
CARTILLAS_DIR.mkdir(exist_ok=True)

def _load_autodraw_settings() -> dict:
    with db_session() as db:
        row = db.query(Config).filter_by(key="_autodraw_sessions").first()
        if not row:
            return {}
        try:
            data = json.loads(row.value)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

def _save_autodraw_settings(settings: dict) -> None:
    clean = settings if isinstance(settings, dict) else {}
    with db_session() as db:
        row = db.query(Config).filter_by(key="_autodraw_sessions").first()
        value = json.dumps(clean, ensure_ascii=False)
        if row:
            row.value = value
        else:
            db.add(Config(key="_autodraw_sessions", value=value))

def _load_sessions() -> list:
    extra = _load_autodraw_settings()
    with db_session() as db:
        rows = db.query(BingoSession).order_by(BingoSession.datetime_iso.desc()).all()
        result = []
        for row in rows:
            item = row.to_dict()
            sid = item.get("id", "")
            cfg = extra.get(sid, {}) if isinstance(extra, dict) else {}
            item.setdefault("auto_start", bool(cfg.get("auto_start", False)))
            item.setdefault("auto_draw_config", cfg.get("auto_draw_config", {}))
            result.append(item)
        return result

def _save_sessions(session_list: list) -> None:
    auto_map = {}
    with db_session() as db:
        for item in session_list or []:
            if not isinstance(item, dict):
                continue
            sid = (item.get("id") or "").strip()
            if not sid:
                continue
            row = db.query(BingoSession).filter_by(id=sid).first()
            if row:
                for key in (
                    "bingo_type", "bingo_nombre", "bingo_color", "bingo_precio",
                    "datetime_iso", "date", "time", "descripcion",
                    "max_players", "status", "created"
                ):
                    if key in item and hasattr(row, key):
                        setattr(row, key, item.get(key))
            auto_map[sid] = {
                "auto_start": bool(item.get("auto_start", False)),
                "auto_draw_config": item.get("auto_draw_config", {}) or {},
            }
    _save_autodraw_settings(auto_map)

def _load_vouchers() -> list:
    with db_session() as db:
        return [v.to_dict() for v in db.query(Voucher).order_by(Voucher.created.desc()).all()]

# ─── Session helpers ──────────────────────────────────────────────────────────
def get_upcoming_sessions(limit: int = 10) -> list:
    now = datetime.now().isoformat()
    with db_session() as db:
        rows = (db.query(BingoSession)
                .filter(BingoSession.datetime_iso >= now,
                        BingoSession.status.notin_(["cancelled", "finished"]))
                .order_by(BingoSession.datetime_iso)
                .limit(limit)
                .all())
        return [r.to_dict() for r in rows]

def get_session(sid: str) -> dict | None:
    with db_session() as db:
        s = db.query(BingoSession).filter_by(id=sid).first()
        return s.to_dict() if s else None

# ─── GameState (in-memory, persisted to SQLite game_state row) ────────────────
class GameState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.available         = list(range(1, 91))
        self.drawn: list       = []
        self.last              = None
        self.game_id           = str(uuid.uuid4())[:8].upper()
        self.claimed_winners   = set()
        self.linea_claimed     = set()
        self.linea_drawn_at    = None   # drawn_count cuando se reclamó la 1ª línea
        self.winners_log       = []
        self.linea_winners_log = []
        self.last_phrase       = None
        self.last_voice        = "es-PE-CamilaNeural"
        self.last_activity     = None
        self.paused            = False
        self.winners_limit     = 1
        self.session_id        = None
        self.bingo_type        = "1sol"
        self.prize_pool        = 0.0
        self.linea_pool        = 0.0
        self.preparing         = False
        self.prepare_at        = None
        self.prepare_secs      = 60
        self.prepare_sid       = None

    def draw(self):
        if not self.available:
            return None
        num = random.choice(self.available)
        self.available.remove(num)
        self.drawn.append(num)
        self.last = num
        return num

    def save_to_db(self):
        """Persist game state into the config table (key='_game_state')."""
        try:
            data = {
                "available": self.available, "drawn": self.drawn,
                "last": self.last, "game_id": self.game_id,
                "claimed_winners": list(self.claimed_winners),
                "linea_claimed":   list(self.linea_claimed),
                "linea_drawn_at":  self.linea_drawn_at,
                "winners_log":          self.winners_log,
                "linea_winners_log":    self.linea_winners_log,
                "last_phrase":     self.last_phrase,
                "last_voice":      self.last_voice,
                "last_activity":   self.last_activity,
                "paused":          self.paused,
                "winners_limit":   self.winners_limit,
                "session_id":      self.session_id,
                "bingo_type":      self.bingo_type,
                "prize_pool":      self.prize_pool,
                "linea_pool":      self.linea_pool,
                "preparing":       self.preparing,
                "prepare_at":      self.prepare_at,
                "prepare_secs":    self.prepare_secs,
                "prepare_sid":     self.prepare_sid,
                "saved_at":        datetime.now().isoformat(),
            }
            value = json.dumps(data, ensure_ascii=False)
            with db_session() as db:
                row = db.query(Config).filter_by(key="_game_state").first()
                if row:
                    row.value = value
                else:
                    db.add(Config(key="_game_state", value=value))
        except Exception as e:
            print(f"[WARN] Error guardando estado: {e}")

    def load_from_db(self):
        try:
            with db_session() as db:
                row = db.query(Config).filter_by(key="_game_state").first()
                if not row:
                    return False
                data = json.loads(row.value)

            saved_sid = data.get("session_id")
            if saved_sid:
                s = get_session(saved_sid)
                if not s or s.get("status") not in ("active", "preparing"):
                    print(f"[WARN] Sesion {saved_sid} ya no activa - limpiando estado.")
                    self._delete_state_row()
                    return False

            self.available         = data.get("available", list(range(1, 91)))
            self.drawn             = data.get("drawn", [])
            self.last              = data.get("last")
            self.game_id           = data.get("game_id", self.game_id)
            self.claimed_winners   = set(data.get("claimed_winners", []))
            self.linea_claimed     = set(data.get("linea_claimed", []))
            self.linea_drawn_at    = data.get("linea_drawn_at", None)
            self.winners_log       = data.get("winners_log", [])
            self.linea_winners_log = data.get("linea_winners_log", [])
            self.last_phrase       = data.get("last_phrase")
            self.last_voice        = data.get("last_voice", "es-PE-CamilaNeural")
            self.last_activity     = data.get("last_activity")
            self.paused            = data.get("paused", False)
            self.winners_limit     = data.get("winners_limit", 1)
            self.session_id        = saved_sid
            self.bingo_type        = data.get("bingo_type", "1sol")
            self.prize_pool        = data.get("prize_pool", 0.0)
            self.linea_pool        = data.get("linea_pool", 0.0)
            self.preparing         = data.get("preparing", False)
            self.prepare_at        = data.get("prepare_at")
            self.prepare_secs      = data.get("prepare_secs", 60)
            self.prepare_sid       = data.get("prepare_sid")
            if self.drawn:
                print(f"[OK] Estado restaurado - {len(self.drawn)} bolillas, sesion {saved_sid}")
            return True
        except Exception as e:
            print(f"[WARN] No se pudo restaurar estado: {e}")
            return False

    def _delete_state_row(self):
        try:
            with db_session() as db:
                row = db.query(Config).filter_by(key="_game_state").first()
                if row:
                    db.delete(row)
        except Exception:
            pass

    # alias kept for compatibility
    def save_to_disk(self):
        self.save_to_db()

    def load_from_disk(self):
        return self.load_from_db()

game      = GameState()
game_lock = threading.Lock()

with game_lock:
    game.load_from_db()

# ─── TTS ──────────────────────────────────────────────────────────────────────
async def _tts_save(text, voice, path):
    await edge_tts.Communicate(text, voice=voice).save(path)

def make_audio(text, voice):
    safe  = "".join(c for c in text.lower() if c.isalnum() or c in " _-").replace(" ", "_")[:60] or "tts"
    fpath = TTS_DIR / f"{voice}_{safe}.mp3"
    if not fpath.exists():
        asyncio.run(_tts_save(text, voice, str(fpath)))
    return fpath

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ─── Generador de cartillas (unchanged) ──────────────────────────────────────
COL_RANGES = [
    list(range(1, 10)), list(range(10, 20)), list(range(20, 30)),
    list(range(30, 40)), list(range(40, 50)), list(range(50, 60)),
    list(range(60, 70)), list(range(70, 80)), list(range(80, 91)),
]

def generate_cartilla_grid():
    for _ in range(1000):
        col_counts = [1] * 9
        extras     = random.sample(range(9), 6)
        for e in extras:
            col_counts[e] = 2
        col_rows = []
        for c in range(9):
            col_rows.append(random.sample([0, 1, 2], col_counts[c]))
        row_counts = [0, 0, 0]
        for c in range(9):
            for r in col_rows[c]:
                row_counts[r] += 1
        if row_counts != [5, 5, 5]:
            continue
        grid = [[None] * 9 for _ in range(3)]
        for c in range(9):
            nums = sorted(random.sample(COL_RANGES[c], col_counts[c]))
            for i, r in enumerate(sorted(col_rows[c])):
                grid[r][c] = nums[i]
        return grid
    raise RuntimeError("No se pudo generar cartilla válida")

def save_cartilla(nombre: str, grid: list, telefono: str = "",
                  voucher_code: str = "", session_id: str = "",
                  bingo_type: str = "1sol",
                  generada_por_admin: bool = False) -> dict:
    cid = str(uuid.uuid4())[:8].upper()
    with db_session() as db:
        row = Cartilla(
            id=cid, nombre=nombre[:60],
            telefono=(telefono or "")[:30],
            voucher_code=(voucher_code or "").strip().upper(),
            session_id=session_id or "",
            bingo_type=bingo_type or "1sol",
            grid=json.dumps(grid, ensure_ascii=False),
            created=datetime.now().isoformat(),
            generada_por_admin=generada_por_admin,
        )
        db.add(row)
    return {
        "id": cid, "nombre": nombre, "telefono": telefono,
        "voucher_code": voucher_code, "session_id": session_id,
        "bingo_type": bingo_type, "grid": grid,
        "created": datetime.now().isoformat(),
        "generada_por_admin": generada_por_admin,
    }

def load_all_cartillas(session_id: str = None) -> list:
    with db_session() as db:
        q = db.query(Cartilla)
        if session_id:
            q = q.filter_by(session_id=session_id)
        return [c.to_dict() for c in q.order_by(Cartilla.created).all()]

def load_cartilla(cid: str) -> dict | None:
    with db_session() as db:
        c = db.query(Cartilla).filter_by(id=cid.upper()).first()
        return c.to_dict() if c else None

def check_winner(grid: list, drawn: list) -> dict:
    drawn_set = set(drawn)
    nums      = [n for row in grid for n in row if n is not None]
    marked    = [n for n in nums if n in drawn_set]
    result    = {
        "total": len(nums), "marked": len(marked),
        "bingo": len(marked) == len(nums),
        "linea": False, "linea_row": None,
        "almost": False, "almost_num": None,
    }
    for i, row in enumerate(grid):
        row_nums = [n for n in row if n is not None]
        if row_nums and all(n in drawn_set for n in row_nums):
            result["linea"]     = True
            result["linea_row"] = i
            break
    if not result["bingo"] and len(nums) - len(marked) == 1:
        result["almost"]     = True
        result["almost_num"] = next(
            (n for row in grid for n in row if n and n not in drawn_set), None)
    return result

def compute_prize_pool(session_id: str, bingo_type: str) -> dict:
    btype     = BINGO_TYPES.get(bingo_type, BINGO_TYPES["1sol"])
    precio    = btype["precio"]
    prize_pct = btype["prize_pct"]
    linea_pct = btype.get("linea_pct", 0.10)
    with db_session() as db:
        paid = (db.query(Voucher)
                .filter_by(session_id=session_id, bingo_type=bingo_type)
                .filter(Voucher.payment_status.in_(["approved", "manual_approved"]))
                .count())
    gross  = paid * precio
    linea  = round(gross * linea_pct, 2)
    prize  = round(gross * prize_pct, 2)
    house  = round(gross - prize - linea, 2)
    return {
        "total_players": paid, "precio_entrada": precio, "gross": gross,
        "prize_pct": prize_pct, "linea_pct": linea_pct,
        "prize_amount": prize, "linea_amount": linea, "house_cut": house,
        "bingo_type": bingo_type, "bingo_nombre": btype["nombre"],
    }

# ─── Font / PDF / PNG (unchanged) ─────────────────────────────────────────────
def _get_font(bold=False, size=16):
    candidates = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
        if bold else
        ["arial.ttf", "DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()

GROUP_COLORS_HEX = [
    "#5dade2", "#f4d03f", "#f1948a", "#e59866",
    "#58d68d", "#a569bd", "#48c9b0", "#7fb3d3", "#95a5a6",
]

def hex_to_rgb01(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

def cartilla_to_pdf(cartilla: dict, drawn: list = None) -> BytesIO:
    drawn_set = set(drawn or [])
    btype     = BINGO_TYPES.get(cartilla.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    buf       = BytesIO()
    cw, ch    = A4
    c         = rl_canvas.Canvas(buf, pagesize=A4)
    r_a, g_a, b_a = hex_to_rgb01(btype["color"])
    c.setFillColorRGB(0.04, 0.07, 0.10)
    c.rect(0, 0, cw, ch, fill=1, stroke=0)
    c.setFillColorRGB(r_a, g_a, b_a)
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(cw / 2, ch - 1.8 * cm, "BINGO PRO")
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(cw / 2, ch - 2.9 * cm, f"{btype['emoji']}  {btype['nombre']}")
    c.setFillColorRGB(0.55, 0.72, 0.85)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(cw / 2, ch - 3.5 * cm,
                        f"Cartilla #{cartilla['id']}  —  {cartilla['nombre']}")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.4, 0.55, 0.65)
    c.drawCentredString(cw / 2, ch - 4.1 * cm,
                        f"Generada: {cartilla.get('created', '')[:16].replace('T', ' ')}  |  Sesión: {cartilla.get('session_id', '') or '—'}")
    if cartilla.get("generada_por_admin"):
        c.setFillColorRGB(0.9, 0.5, 0.1)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(cw / 2, ch - 4.5 * cm, "⚠ Generada por administrador")
    grid  = cartilla["grid"]
    col_w = (cw - 4 * cm) / 9
    row_h = 1.8 * cm
    x0    = 2 * cm
    y0    = ch - 5.7 * cm
    col_labels = ["1-10","11-20","21-30","31-40","41-50","51-60","61-70","71-80","81-90"]
    for ci in range(9):
        r, g, b = hex_to_rgb01(GROUP_COLORS_HEX[ci])
        cx = x0 + ci * col_w
        c.setFillColorRGB(r * .3, g * .3, b * .3)
        c.roundRect(cx + 1, y0 + 2, col_w - 2, 0.7 * cm, 4, fill=1, stroke=0)
        c.setFillColorRGB(r, g, b)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(cx + col_w / 2, y0 + 0.2 * cm + 2, col_labels[ci])
    y0 -= 0.8 * cm
    for ri in range(3):
        for ci in range(9):
            num     = grid[ri][ci]
            cx      = x0 + ci * col_w
            cy      = y0 - ri * row_h
            r, g, b = hex_to_rgb01(GROUP_COLORS_HEX[ci])
            if num is None:
                c.setFillColorRGB(.07, .12, .16); c.setStrokeColorRGB(.10, .18, .24)
            elif num in drawn_set:
                c.setFillColorRGB(r * .5, g * .5, b * .5); c.setStrokeColorRGB(r, g, b)
            else:
                c.setFillColorRGB(.07, .16, .22); c.setStrokeColorRGB(.15, .28, .38)
            c.roundRect(cx + 2, cy - row_h + 4, col_w - 4, row_h - 6, 6, fill=1, stroke=1)
            if num is not None:
                if num in drawn_set:
                    c.setFillColorRGB(r, g, b)
                    c.circle(cx + col_w / 2, cy - row_h / 2 + 2, min(col_w, row_h) * .36, fill=1, stroke=0)
                    c.setFillColorRGB(.04, .07, .10)
                else:
                    c.setFillColorRGB(min(1, r * 1.2), min(1, g * 1.2), min(1, b * 1.2))
                c.setFont("Helvetica-Bold", 18)
                c.drawCentredString(cx + col_w / 2, cy - row_h / 2 - 5, str(num))
    c.setFillColorRGB(r_a * .8, g_a * .8, b_a * .8)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(cw / 2, 3.6 * cm, f"{btype['nombre']}  -  S/. {btype['precio']:.2f}")
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(.4, .55, .65)
    c.drawCentredString(cw / 2, 3.0 * cm,
                        f"Premio bingo: {int(btype['prize_pct'] * 100)}%  |  Premio línea: {int(btype.get('linea_pct', 0) * 100)}%")
    qr     = qrcode.make(f"BINGO-{cartilla['id']}")
    qr_buf = BytesIO(); qr.save(qr_buf, format="PNG"); qr_buf.seek(0)
    qr_size = 2.5 * cm
    c.drawImage(ImageReader(qr_buf), cw - 3 * cm, 1.5 * cm, width=qr_size, height=qr_size,
                preserveAspectRatio=True, mask="auto")
    c.setFillColorRGB(.25, .40, .52)
    c.setFont("Helvetica", 9)
    marked_count = len([n for row in grid for n in row if n and n in drawn_set])
    c.drawCentredString(cw / 2, 2.0 * cm, "Bingo Pro Web v8.0  •  Made by Renso Ramirez")
    c.drawCentredString(cw / 2, 1.4 * cm, f"Números marcados: {marked_count} / 15")
    c.save(); buf.seek(0)
    return buf

def cartilla_to_png(cartilla: dict, drawn: list = None) -> BytesIO:
    drawn_set = set(drawn or [])
    grid  = cartilla["grid"]
    btype = BINGO_TYPES.get(cartilla.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    COLS, ROWS = 9, 3
    PAD = 30; HEADER_H = 150; FOOTER_H = 80; CW, CH = 90, 80
    W = PAD * 2 + COLS * CW
    H = PAD * 2 + HEADER_H + ROWS * CH + FOOTER_H
    img  = Image.new("RGB", (W, H), (10, 18, 26))
    draw = ImageDraw.Draw(img)
    accent_hex = btype["color"].lstrip("#")
    accent_rgb = tuple(int(accent_hex[i:i+2], 16) for i in (0, 2, 4))
    draw.text((W // 2, PAD + 6), f"BINGO PRO — {btype['nombre']}",
              fill=accent_rgb, anchor="mt", font=_get_font(bold=True, size=30))
    draw.text((W // 2, PAD + 48), f"Cartilla #{cartilla['id']}  —  {cartilla['nombre']}",
              fill=(140, 180, 210), anchor="mt", font=_get_font(size=15))
    draw.text((W // 2, PAD + 72), f"Precio: S/. {btype['precio']:.2f}  |  Premio: {int(btype['prize_pct'] * 100)}%",
              fill=(100, 140, 170), anchor="mt", font=_get_font(size=12))
    GROUP_RGB = [(93,173,226),(244,208,63),(241,148,138),(229,152,102),
                 (88,214,141),(165,105,189),(72,201,176),(127,179,211),(149,165,166)]
    col_labels = ["1-10","11-20","21-30","31-40","41-50","51-60","61-70","71-80","81-90"]
    y_start = PAD + HEADER_H
    for ci in range(COLS):
        cx    = PAD + ci * CW
        r,g,b = GROUP_RGB[ci]
        draw.rounded_rectangle([cx + 3, y_start - 26, cx + CW - 3, y_start - 4],
                                radius=6, fill=(int(r * .25), int(g * .25), int(b * .25)))
        draw.text((cx + CW // 2, y_start - 15), col_labels[ci],
                  fill=(r, g, b), anchor="mm", font=_get_font(bold=True, size=11))
    for ri in range(ROWS):
        for ci in range(COLS):
            num   = grid[ri][ci]
            cx    = PAD + ci * CW
            cy    = y_start + ri * CH
            r,g,b = GROUP_RGB[ci]
            if num is None:
                cf, bf = (12,22,32),(20,40,55)
            elif num in drawn_set:
                cf = (int(r*.45),int(g*.45),int(b*.45)); bf = (r,g,b)
            else:
                cf, bf = (14,32,46),(30,65,90)
            draw.rounded_rectangle([cx+4,cy+4,cx+CW-4,cy+CH-4], radius=10, fill=cf, outline=bf, width=2)
            if num is not None:
                if num in drawn_set:
                    m = 12; draw.ellipse([cx+m,cy+m,cx+CW-m,cy+CH-m], fill=(r,g,b))
                    tc = (15,25,35)
                else:
                    tc = (min(255,int(r*1.1)),min(255,int(g*1.1)),min(255,int(b*1.1)))
                draw.text((cx+CW//2,cy+CH//2), str(num), fill=tc,
                          anchor="mm", font=_get_font(bold=True, size=26))
    fy = y_start + ROWS * CH + 12
    draw.text((W // 2, fy), f"ID: {cartilla['id']}", fill=(100,150,180),
              anchor="mt", font=_get_font(size=13))
    draw.text((W // 2, fy + 22), "Bingo Pro Web v8.0  •  Made by Renso Ramirez",
              fill=(60,90,110), anchor="mt", font=_get_font(size=11))
    buf = BytesIO(); img.save(buf, format="PNG", dpi=(150,150)); buf.seek(0)
    return buf

# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES (identical to v7.2 — zero frontend changes needed)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html", is_admin=is_admin())

@app.route("/cartillas")
def cartillas_player_page():
    return render_template("cartillas_player.html")

@app.route("/admin/login")
def admin_login_page():
    if is_admin(): return redirect("/admin")
    return render_template("admin_login.html")

@app.route("/admin")
def admin_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin.html")

@app.route("/admin/cartillas")
def admin_cartillas_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("cartillas_admin.html")

@app.route("/admin/game")
def admin_game_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_game.html")

@app.route("/admin/sessions")
def admin_sessions_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_sessions.html")

@app.route("/admin/payments")
def admin_payments_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_payments.html")

@app.route("/admin/config")
def admin_config_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_config.html")

@app.route("/admin/caja")
def admin_caja_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_caja.html")

# ─── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/api/admin/login", methods=["POST"])
@rate_limit(max_calls=10, window_seconds=60)
def api_admin_login():
    ip   = get_real_ip()
    data = request.get_json() or {}

    if not check_honeypot(data, ip):
        return jsonify({"error": "forbidden"}), 403

    allowed, reason = check_ip_allowed(ip)
    if not allowed:
        return jsonify({"error": "locked", "message": reason}), 429

    username  = (data.get("username") or "").strip()
    password  = (data.get("password") or "")
    totp_code = (data.get("totp")    or "").strip()

    if not ADMIN_USER or not ADMIN_PASS:
        return jsonify({"error": "admin_not_configured"}), 503

    creds_ok = (username == ADMIN_USER and password == ADMIN_PASS)
    if not creds_ok:
        bf = record_failed_login(ip)
        time.sleep(0.6)
        msg = "Usuario o contraseña incorrectos."
        if bf["locked"]:
            msg = "Demasiados intentos. Cuenta bloqueada temporalmente."
        return jsonify({"error": "invalid_credentials", "message": msg}), 401

    if TOTP_SECRET:
        if not totp_code:
            return jsonify({
                "error": "totp_required",
                "message": "Ingresa el código de tu app autenticadora.",
                "requires_totp": True,
            }), 401
        if not totp_verify(TOTP_SECRET, totp_code):
            record_failed_login(ip)
            return jsonify({"error": "totp_invalid",
                            "message": "Código 2FA incorrecto o expirado."}), 401

    record_success_login(ip)
    session["is_admin"]   = True
    session["admin_user"] = username
    session["login_ip"]   = ip
    session["login_at"]   = datetime.now().isoformat()
    return jsonify({"status": "ok"})

@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.clear()
    return jsonify({"status": "ok"})

@app.route("/api/auth/status")
def api_auth_status():
    return jsonify({"is_admin": is_admin()})

# ─── Player solicitar ─────────────────────────────────────────────────────────
@app.route("/api/player/solicitar", methods=["POST"])
@rate_limit(max_calls=5, window_seconds=60)
def api_player_solicitar():
    data           = request.get_json() or {}
    nombres        = (data.get("nombres")       or "").strip()[:60]
    apellidos      = (data.get("apellidos")     or "").strip()[:60]
    email          = (data.get("email")         or "").strip()[:100].lower()
    celular        = (data.get("celular")       or "").strip()[:20]
    yape_plin      = (data.get("yape_plin")     or "").strip()[:20]
    terms_accepted = bool(data.get("terms_accepted", False))
    bingo_type     = data.get("bingo_type", "1sol")
    session_id     = data.get("session_id", "")
    method         = (data.get("method")        or "").strip()
    ref            = (data.get("reference")     or "").strip()[:80]

    if not nombres:
        return jsonify({"error": "missing_name", "message": "Ingresa tu nombre."}), 400
    if not email or "@" not in email:
        return jsonify({"error": "missing_email", "message": "Ingresa un email válido."}), 400
    if not yape_plin:
        return jsonify({"error": "missing_yape_plin", "message": "Ingresa tu número de Yape/Plin para recibir tu premio."}), 400
    if not terms_accepted:
        return jsonify({"error": "terms_not_accepted", "message": "Debes aceptar los términos y condiciones."}), 400
    if bingo_type not in BINGO_TYPES:
        return jsonify({"error": "invalid_bingo_type"}), 400
    # Si se especificó sesión, el bingo_type debe coincidir con el de la sesión
    if session_id:
        s = get_session(session_id)
        if not s:
            return jsonify({"error": "invalid_session",
                            "message": "La sesión seleccionada no existe."}), 400
        if s.get("bingo_type") != bingo_type:
            return jsonify({"error": "bingo_type_mismatch",
                            "message": f"Esta sesión es de {s.get('bingo_nombre')}. "
                                       f"Selecciona el tipo correcto."}), 400
    if method != "efectivo" and not ref:
        return jsonify({"error": "missing_ref", "message": "Ingresa el número de operación."}), 400
    if method != "efectivo" and ref and _is_duplicate_payment_ref(ref, method, session_id):
        return jsonify({"error": "duplicate_reference",
                        "message": "Este número de operación ya fue registrado."}), 400

    btype = BINGO_TYPES[bingo_type]
    v_dict = {}
    with db_session() as db:
        code = _unique_code(db)
        v = Voucher(
            code=code, nombres=nombres, apellidos=apellidos,
            email=email, celular=celular,
            yape_plin=yape_plin, terms_accepted=terms_accepted,
            bingo_type=bingo_type, bingo_nombre=btype["nombre"],
            precio=btype["precio"], session_id=session_id,
            payment_method=method, payment_ref=ref,
            payment_status="pending_review",
            payment_submitted_at=datetime.now().isoformat(),
            created=datetime.now().isoformat(),
            creado_por="jugador",
        )
        db.add(v)
        db.flush()
        v_dict = v.to_dict()

    if email:
        asunto  = f"Recibimos tu solicitud - {btype['nombre']} | Bingo Pro"
        ok, err = enviar_email(email, asunto, _email_pago_recibido(v_dict))
        if not ok:
            print(f"[WARN] Email no enviado a {email}: {err}")

    return jsonify({"status": "ok",
                    "message": "Solicitud registrada. Recibirás tu código cuando el admin confirme el pago.",
                    "email": email})

# ─── Vouchers admin ───────────────────────────────────────────────────────────
@app.route("/api/admin/voucher", methods=["POST"])
def api_admin_create_voucher():
    chk = admin_required()
    if chk: return chk
    data       = request.get_json() or {}
    bingo_type = data.get("bingo_type", "1sol")
    if bingo_type not in BINGO_TYPES:
        return jsonify({"error": "invalid_bingo_type"}), 400
    btype  = BINGO_TYPES[bingo_type]
    v_dict = {}
    with db_session() as db:
        code = _unique_code(db)
        v = Voucher(
            code=code,
            nombres=(data.get("nombres")   or "").strip()[:60],
            apellidos=(data.get("apellidos") or "").strip()[:60],
            email=(data.get("email")        or "").strip()[:100].lower(),
            celular=(data.get("celular")    or "").strip()[:20],
            bingo_type=bingo_type, bingo_nombre=btype["nombre"],
            precio=btype["precio"],
            session_id=data.get("session_id", ""),
            payment_method=data.get("payment_method", "efectivo"),
            payment_status="pending",
            payment_ref=data.get("payment_ref", ""),
            created=datetime.now().isoformat(),
            creado_por="admin",
        )
        db.add(v)
        db.flush()
        v_dict = v.to_dict()
    return jsonify({"status": "ok", "voucher": v_dict})

@app.route("/api/admin/vouchers")
def api_admin_list_vouchers():
    chk = admin_required()
    if chk: return chk
    session_id = request.args.get("session_id", "")
    with db_session() as db:
        q = db.query(Voucher)
        if session_id:
            q = q.filter_by(session_id=session_id)
        vs = [v.to_dict() for v in q.order_by(Voucher.created.desc()).all()]
    return jsonify({"vouchers": vs})

@app.route("/api/admin/voucher/<code>/approve", methods=["POST"])
def api_approve_voucher(code):
    chk = admin_required()
    if chk: return chk
    code = code.strip().upper()
    data = request.get_json() or {}
    v_dict = {}
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        if not v:
            return jsonify({"error": "not found"}), 404
        v.payment_status = "manual_approved"
        v.approved_at    = datetime.now().isoformat()
        v.approved_note  = data.get("note", "")
        db.flush()
        v_dict = v.to_dict()

    email_result = {"sent": False, "error": ""}
    email        = v_dict.get("email", "")
    if email:
        url_base = request.host_url.rstrip("/")
        asunto   = f"🎱 Tu código para {v_dict.get('bingo_nombre', 'Bingo')} — Bingo Pro"
        ok, err  = enviar_email(email, asunto, _email_codigo_voucher(v_dict, url_base))
        email_result = {"sent": ok, "error": err}
        if ok:
            with db_session() as db:
                v2 = db.query(Voucher).filter_by(code=code).first()
                if v2:
                    v2.email_codigo_enviado = True
                    v2.email_enviado_at     = datetime.now().isoformat()
        else:
            print(f"[WARN] Email no enviado a {email}: {err}")

    return jsonify({"status": "ok", "voucher": v_dict, "email_result": email_result})

@app.route("/api/admin/voucher/<code>/reject", methods=["POST"])
def api_reject_voucher(code):
    chk = admin_required()
    if chk: return chk
    code = code.strip().upper()
    data = request.get_json() or {}
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        if not v:
            return jsonify({"error": "not found"}), 404
        v.payment_status  = "rejected"
        v.rejected_at     = datetime.now().isoformat()
        v.rejected_reason = data.get("reason", "")
    return jsonify({"status": "ok"})

@app.route("/api/admin/voucher/<code>/delete", methods=["DELETE"])
def api_admin_delete_voucher(code):
    chk = admin_required()
    if chk: return chk
    code = code.strip().upper()
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        if v:
            db.delete(v)
    return jsonify({"status": "ok"})

@app.route("/api/admin/voucher/<code>/resend_email", methods=["POST"])
def api_resend_email(code):
    chk = admin_required()
    if chk: return chk
    code   = code.strip().upper()
    v_dict = get_voucher_info(code)
    if not v_dict:
        return jsonify({"error": "not found"}), 404
    if v_dict.get("payment_status") not in ("approved", "manual_approved"):
        return jsonify({"error": "not_approved", "message": "El pago no está aprobado."}), 400
    email = v_dict.get("email", "")
    if not email:
        return jsonify({"error": "no_email", "message": "Sin email registrado."}), 400
    url_base = request.host_url.rstrip("/")
    asunto   = f"🎱 [Reenvío] Tu código — {v_dict.get('bingo_nombre', 'Bingo')} | Bingo Pro"
    ok, err  = enviar_email(email, asunto, _email_codigo_voucher(v_dict, url_base))
    if ok:
        with db_session() as db:
            v = db.query(Voucher).filter_by(code=code).first()
            if v:
                v.email_codigo_enviado = True
                v.email_reenviado_at   = datetime.now().isoformat()
        return jsonify({"status": "ok", "message": f"Email reenviado a {email}"})
    return jsonify({"status": "error", "message": err}), 500

@app.route("/api/admin/email/test", methods=["POST"])
def api_test_email():
    chk = admin_required()
    if chk: return chk
    if not email_configurado():
        return jsonify({"status": "error", "message": "EMAIL_FROM y EMAIL_PASS no configurados."}), 400
    data  = request.get_json() or {}
    dest  = (data.get("email") or EMAIL_FROM).strip()
    ok, err = enviar_email(
        dest, "✅ Test de email — Bingo Pro",
        f"<html><body style='font-family:Arial;background:#070d14;color:#ddeeff;padding:32px'>"
        f"<h2 style='color:#00e5b4'>🎱 Test de email exitoso</h2>"
        f"<p>Enviado desde: {EMAIL_FROM}<br>SMTP: {SMTP_HOST}:{SMTP_PORT}</p></body></html>"
    )
    if ok:
        return jsonify({"status": "ok", "message": f"Email de prueba enviado a {dest}"})
    return jsonify({"status": "error", "message": err}), 500

# ─── Voucher status (player polling) ──────────────────────────────────────────
@app.route("/api/voucher/check", methods=["POST"])
@rate_limit(max_calls=20, window_seconds=60)
def api_voucher_check():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    ok, err = validate_voucher_for_cartilla(code)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    vinfo = get_voucher_info(code)
    btype = BINGO_TYPES.get(vinfo.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    return jsonify({"ok": True, "voucher": {
        "bingo_type":                vinfo.get("bingo_type"),
        "bingo_nombre":              vinfo.get("bingo_nombre"),
        "session_id":                vinfo.get("session_id"),
        "max_cartillas_per_voucher": btype.get("max_cartillas_per_voucher", 5),
        "cartillas_generadas":       len(vinfo.get("cartillas", [])),
    }})

@app.route("/api/voucher/status", methods=["POST"])
@rate_limit(max_calls=30, window_seconds=60)
def api_voucher_status():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": "missing_code"}), 400
    v = get_voucher_info(code)
    if not v:
        return jsonify({"error": "not_found"}), 404
    btype    = BINGO_TYPES.get(v.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    ya_c     = len(v.get("cartillas", []))
    max_c    = btype.get("max_cartillas_per_voucher", 5)
    approved = v.get("payment_status") in ("approved", "manual_approved")
    return jsonify({
        "code":                v["code"],
        "payment_status":      v.get("payment_status", "pending"),
        "bingo_type":          v.get("bingo_type"),
        "bingo_nombre":        v.get("bingo_nombre"),
        "bingo_color":         btype.get("color"),
        "session_id":          v.get("session_id"),
        "cartillas_generadas": ya_c,
        "max_cartillas":       max_c,
        "puede_generar":       approved and ya_c < max_c,
        "approved_at":         v.get("approved_at", ""),
        "rejected_reason":     v.get("rejected_reason", ""),
        "email_enviado":       v.get("email_codigo_enviado", False),
    })

@app.route("/api/payment/register", methods=["POST"])
@rate_limit(max_calls=5, window_seconds=60)
def api_register_payment():
    data      = request.get_json() or {}
    code      = (data.get("code")      or "").strip().upper()
    method    = (data.get("method")    or "transferencia").strip()
    ref       = (data.get("reference") or "").strip()[:80]
    nombres   = (data.get("nombres")   or "").strip()[:60]
    apellidos = (data.get("apellidos") or "").strip()[:60]
    email     = (data.get("email")     or "").strip()[:100].lower()
    if not code:
        return jsonify({"error": "missing_code"}), 400
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        if not v:
            return jsonify({"error": "bad_code"}), 400
        if ref and _is_duplicate_payment_ref(ref, method, v.session_id):
            return jsonify({"error": "duplicate_reference",
                            "message": "Este número de operación ya fue registrado."}), 400
        v.payment_method       = method
        v.payment_ref          = ref
        v.nombres              = nombres or v.nombres
        v.apellidos            = apellidos or v.apellidos
        if email:
            v.email            = email
        v.payment_submitted_at = datetime.now().isoformat()
        if v.payment_status not in ("approved", "manual_approved"):
            v.payment_status   = "pending_review"
        status = v.payment_status
    return jsonify({"status": "ok", "payment_status": status, "code": code})

# ─── Game API (unchanged logic) ───────────────────────────────────────────────
@app.route("/api/draw", methods=["POST"])
def api_draw():
    chk = admin_required()
    if chk: return chk
    with game_lock:
        if not game.available:
            return jsonify({"status": "finished", "drawn": game.drawn})
        if game.paused:
            return jsonify({"status": "paused", "winners": game.winners_log, "drawn": game.drawn}), 200
        num   = game.draw()
        words = num2words(num, lang="es")
        count = len(game.drawn)
        if count == 1:
            phrase = f"Primera bolilla, número {words}"
        elif count == 90:
            phrase = f"Última bolilla, número {words}. ¡Juego completo!"
        else:
            phrase = f"La siguiente bolilla es el número {words}"
        game.last_phrase   = phrase
        game.last_activity = time.time()
        wc = len(game.claimed_winners)
        if wc >= game.winners_limit and not game.paused:
            game.paused = True
        result = {
            "status": "ok", "number": num, "words": words, "phrase": phrase,
            "drawn": list(game.drawn), "remaining": len(game.available), "count": count,
        }
        game.save_to_db()
    try:
        voice = (request.get_json(silent=True) or {}).get("voice", "es-PE-CamilaNeural")
    except Exception:
        voice = "es-PE-CamilaNeural"
    with game_lock:
        game.last_voice = voice
    return jsonify(result)

@app.route("/api/speak", methods=["POST"])
def api_speak():
    data  = request.get_json() or {}
    text  = data.get("text", "")
    voice = data.get("voice", "es-MX-DaliaNeural")
    if not text:
        return jsonify({"error": "no text"}), 400
    try:
        return send_file(make_audio(text, voice), mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/repeat", methods=["POST"])
def api_repeat():
    chk = admin_required()
    if chk: return chk
    data  = request.get_json() or {}
    voice = data.get("voice", "es-MX-DaliaNeural")
    with game_lock:
        if game.last is None:
            return jsonify({"error": "no number"}), 400
        phrase = f"Repito, bolilla número {num2words(game.last, lang='es')}"
    try:
        return send_file(make_audio(phrase, voice), mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/reset", methods=["POST"])
def api_reset():
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}
    with game_lock:
        prev_sid = game.session_id
        game.reset()
        if data.get("session_id"): game.session_id = data["session_id"]
        if data.get("bingo_type"): game.bingo_type = data["bingo_type"]
        game.save_to_db()
    # Si había una sesión activa, marcarla como cancelada en DB
    if prev_sid:
        with db_session() as db:
            sx = db.query(BingoSession).filter_by(id=prev_sid).first()
            if sx and sx.status == "active":
                sx.status = "cancelled"
    return jsonify({"status": "ok"})

@app.route("/api/admin/reset_state", methods=["POST"])
def api_reset_state():
    chk = admin_required()
    if chk: return chk
    with game_lock:
        game.reset()
        game._delete_state_row()
    return jsonify({"status": "ok", "message": "Estado del juego limpiado correctamente."})

@app.route("/api/state")
def api_state():
    with game_lock:
        la    = game.last_activity
        ao    = la is None or (time.time() - la) < 300
        bt_id = game.bingo_type
        bt_info = BINGO_TYPES.get(bt_id, BINGO_TYPES["1sol"])
        return jsonify({
            "drawn":          game.drawn,
            "remaining":      len(game.available),
            "last":           game.last,
            "game_id":        game.game_id,
            "last_phrase":    game.last_phrase,
            "last_voice":     game.last_voice,
            "last_activity":  la,
            "admin_online":   ao,
            "paused":         game.paused,
            "winners":            game.winners_log,
            "linea_winners":      game.linea_winners_log,
            "winners_count":      len(game.claimed_winners),
            "winners_limit":      game.winners_limit,
            "prize_per_winner": (
                round(game.prize_pool / max(1, len(game.claimed_winners)), 2)
                if game.claimed_winners else game.prize_pool
            ),
            "session_id":     game.session_id,
            "bingo_type":     bt_id,
            "bingo_nombre":   bt_info["nombre"],
            "bingo_color":    bt_info["color"],
            "bingo_precio":   bt_info["precio"],
            "prize_pool":     game.prize_pool,
            "linea_pool":     game.linea_pool,
            "preparing":      game.preparing,
            "prepare_at":     game.prepare_at,
            "prepare_secs":   game.prepare_secs,
            "prepare_sid":    game.prepare_sid,
        })

@app.route("/api/admin/resume", methods=["POST"])
def api_admin_resume():
    chk = admin_required()
    if chk: return chk
    with game_lock:
        game.paused = False
        game.save_to_db()
    return jsonify({"status": "ok", "paused": False})

@app.route("/api/admin/winners_limit", methods=["POST"])
def api_admin_winners_limit():
    chk = admin_required()
    if chk: return chk
    data  = request.get_json() or {}
    limit = max(1, min(int(data.get("limit", 1)), 10))
    with game_lock:
        game.winners_limit = limit
    return jsonify({"status": "ok", "winners_limit": limit})

@app.route("/api/admin/set_bingo_type", methods=["POST"])
def api_set_bingo_type():
    chk = admin_required()
    if chk: return chk
    data  = request.get_json() or {}
    btype = data.get("bingo_type", "1sol")
    if btype not in BINGO_TYPES:
        return jsonify({"error": "invalid bingo_type"}), 400
    with game_lock:
        game.bingo_type = btype
    return jsonify({"status": "ok", "bingo_type": btype})

# ─── Sessions API ─────────────────────────────────────────────────────────────
@app.route("/api/admin/sessions", methods=["GET"])
def api_list_sessions():
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        rows = db.query(BingoSession).order_by(BingoSession.datetime_iso.desc()).all()
        ss   = [r.to_dict() for r in rows]
    return jsonify({"sessions": ss})

@app.route("/api/sessions/upcoming", methods=["GET"])
def api_upcoming_sessions():
    return jsonify({"sessions": get_upcoming_sessions(20)})

@app.route("/api/admin/session", methods=["POST"])
def api_create_session():
    chk = admin_required()
    if chk: return chk
    data       = request.get_json() or {}
    bingo_type = data.get("bingo_type", "1sol")
    if bingo_type not in BINGO_TYPES:
        return jsonify({"error": "invalid bingo_type"}), 400
    dt_str = data.get("datetime")
    if not dt_str:
        return jsonify({"error": "datetime required"}), 400
    btype = BINGO_TYPES[bingo_type]
    sid   = str(uuid.uuid4())[:8].upper()
    s_dict = {}
    with db_session() as db:
        s = BingoSession(
            id=sid, bingo_type=bingo_type,
            bingo_nombre=btype["nombre"], bingo_color=btype["color"],
            bingo_precio=btype["precio"], datetime_iso=dt_str,
            date=dt_str[:10],
            time=dt_str[11:16] if len(dt_str) >= 16 else "00:00",
            descripcion=data.get("descripcion", ""),
            max_players=int(data.get("max_players", 0)),
            status="scheduled",
            created=datetime.now().isoformat(),
        )
        db.add(s)
        db.flush()
        s_dict = s.to_dict()
    return jsonify({"status": "ok", "session": s_dict})

@app.route("/api/admin/session/<sid>/start", methods=["POST"])
def api_start_session(sid):
    chk = admin_required()
    if chk: return chk
    s_dict = {}
    prize  = {}
    with db_session() as db:
        s = db.query(BingoSession).filter_by(id=sid).first()
        if not s:
            return jsonify({"error": "not found"}), 404
        if s.status not in ("scheduled", "preparing"):
            return jsonify({"error": "invalid_status",
                            "message": "La sesión no está en estado válido para iniciar."}), 400
        s.status     = "active"
        s.started_at = datetime.now().isoformat()
        db.flush()
        prize = compute_prize_pool(sid, s.bingo_type)
        s.prize_info = json.dumps(prize, ensure_ascii=False)
        s.prepare_at = None
        db.flush()
        s_dict = s.to_dict()

    with game_lock:
        game.reset()
        game.session_id  = sid
        game.bingo_type  = s_dict["bingo_type"]
        game.prize_pool  = prize["prize_amount"]
        game.linea_pool  = prize["linea_amount"]
        game.preparing   = False
        game.prepare_at  = None
        game.prepare_sid = None
        game.save_to_db()
    return jsonify({"status": "ok", "session": s_dict, "prize_info": prize})

@app.route("/api/admin/session/<sid>/prepare", methods=["POST"])
def api_prepare_session(sid):
    chk = admin_required()
    if chk: return chk
    data     = request.get_json() or {}
    segundos = max(10, min(int(data.get("segundos", 60)), 300))
    s        = get_session(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    if s.get("status") not in ("scheduled",):
        return jsonify({"error": "session_not_scheduled",
                        "message": "Solo se puede preparar una sesión programada."}), 400

    with db_session() as db:
        sx = db.query(BingoSession).filter_by(id=sid).first()
        if sx:
            sx.status       = "preparing"
            sx.prepare_at   = datetime.now().isoformat()
            sx.prepare_secs = segundos

    with game_lock:
        game.preparing    = True
        game.prepare_at   = time.time()
        game.prepare_secs = segundos
        game.prepare_sid  = sid
        game.save_to_db()

    with db_session() as db:
        jugadores_email = (db.query(Voucher)
                           .filter_by(session_id=sid)
                           .filter(Voucher.payment_status.in_(["approved", "manual_approved"]))
                           .filter(Voucher.email != "")
                           .all())
        j_list = [v.to_dict() for v in jugadores_email]

    n        = len(j_list)
    url_base = request.host_url.rstrip("/")
    s_snap   = dict(s)

    def _enviar_masivo():
        enviados = 0
        for v in j_list:
            mins   = segundos // 60
            tiempo = f"{mins}min" if segundos >= 60 else f"{segundos}s"
            asunto = f"⏰ ¡El bingo empieza en {tiempo}! — {s_snap.get('bingo_nombre', 'Bingo Pro')}"
            ok, _  = enviar_email(v["email"], asunto,
                                  _email_aviso_inicio(v, s_snap, url_base, segundos))
            if ok:
                enviados += 1
            time.sleep(0.25)
        print(f"[OK] Emails de aviso enviados: {enviados}/{n}")

    threading.Thread(target=_enviar_masivo, daemon=True).start()
    return jsonify({
        "status": "ok", "prepare_at": game.prepare_at,
        "prepare_secs": segundos, "emails_enviando": n,
        "message": f"Countdown de {segundos}s iniciado. Enviando email a {n} jugador(es).",
    })

@app.route("/api/admin/session/<sid>/cancel_prepare", methods=["POST"])
def api_cancel_prepare(sid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        sx = db.query(BingoSession).filter_by(id=sid).first()
        if sx and sx.status == "preparing":
            sx.status     = "scheduled"
            sx.prepare_at = None
    with game_lock:
        game.preparing   = False
        game.prepare_at  = None
        game.prepare_sid = None
        game.save_to_db()
    return jsonify({"status": "ok", "message": "Countdown cancelado."})

@app.route("/api/admin/session/<sid>/finish", methods=["POST"])
def api_finish_session(sid):
    chk = admin_required()
    if chk: return chk
    with game_lock:
        wf = list(game.winners_log)
    with db_session() as db:
        sx = db.query(BingoSession).filter_by(id=sid).first()
        if not sx:
            return jsonify({"error": "not found"}), 404
        sx.status       = "finished"
        sx.finished_at  = datetime.now().isoformat()
        sx.winners_final= json.dumps(wf, ensure_ascii=False)
    # Limpiar game state si era la sesión activa
    with game_lock:
        if game.session_id == sid:
            game.session_id = None
            game.save_to_db()
    return jsonify({"status": "ok"})

@app.route("/api/admin/session/<sid>/cancel", methods=["POST"])
def api_cancel_session(sid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        sx = db.query(BingoSession).filter_by(id=sid).first()
        if not sx:
            return jsonify({"error": "not found"}), 404
        sx.status = "cancelled"
    # Limpiar game state si era la sesión activa
    with game_lock:
        if game.session_id == sid:
            game.session_id = None
            game.save_to_db()
    return jsonify({"status": "ok"})

@app.route("/api/admin/session/<sid>/delete", methods=["DELETE"])
def api_delete_session(sid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        sx = db.query(BingoSession).filter_by(id=sid).first()
        if not sx:
            return jsonify({"error": "not found"}), 404
        if sx.status in ("active", "preparing"):
            return jsonify({"error": "cannot_delete_active",
                            "message": "No se puede eliminar una sesión activa o en countdown."}), 400
        db.delete(sx)
    return jsonify({"status": "ok"})

@app.route("/api/admin/session/<sid>/prize", methods=["GET"])
def api_session_prize(sid):
    chk = admin_required()
    if chk: return chk
    s = get_session(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    return jsonify(compute_prize_pool(sid, s["bingo_type"]))

# ─── Winners history ──────────────────────────────────────────────────────────
@app.route("/api/winners/history")
def api_winners_history():
    limit = min(int(request.args.get("limit", 20)), 50)
    with db_session() as db:
        rows = (db.query(BingoSession)
                .filter_by(status="finished")
                .order_by(BingoSession.finished_at.desc())
                .limit(limit)
                .all())
        result = []
        for s in rows:
            wf = []
            try:
                wf = json.loads(s.winners_final or "[]")
            except Exception:
                pass
            for w in wf:
                result.append({
                    "session_id":    s.id,
                    "bingo_nombre":  s.bingo_nombre,
                    "date":          s.date,
                    "winner_nombre": w.get("nombre", ""),
                    "drawn_count":   w.get("drawn_count", 0),
                    "prize":         w.get("prize", 0),
                })
    return jsonify({"winners": result})

# ─── Caja ─────────────────────────────────────────────────────────────────────
@app.route("/api/admin/caja")
def api_caja():
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        sessions = db.query(BingoSession).order_by(BingoSession.datetime_iso.desc()).all()
        vouchers = db.query(Voucher).all()

    total_rec  = sum(v.precio for v in vouchers
                     if v.payment_status in ("approved", "manual_approved"))
    total_prem = 0.0
    sesiones   = []
    for s in sessions:
        wf = []
        try: wf = json.loads(s.winners_final or "[]")
        except Exception: pass
        premios   = sum(w.get("prize", 0) for w in wf)
        total_prem += premios
        sid_vs    = [v for v in vouchers
                     if v.session_id == s.id
                     and v.payment_status in ("approved", "manual_approved")]
        recaudado = sum(v.precio for v in sid_vs)
        sesiones.append({
            "id": s.id, "fecha": s.date, "hora": s.time,
            "bingo_type": s.bingo_type, "bingo_nombre": s.bingo_nombre,
            "status": s.status, "jugadores": len(sid_vs),
            "recaudado": round(recaudado, 2),
            "premios_paid": round(premios, 2),
            "ganancia": round(recaudado - premios, 2),
            "ganadores": wf,
        })

    return jsonify({
        "resumen": {
            "total_recaudado": round(total_rec, 2),
            "total_premios":   round(total_prem, 2),
            "ganancia_bruta":  round(total_rec - total_prem, 2),
            "total_sesiones":  len(sessions),
            "total_vouchers":  len(vouchers),
        },
        "sesiones": sesiones,
    })

# ─── Cartillas API ────────────────────────────────────────────────────────────
@app.route("/api/cartilla/generate", methods=["POST"])
@rate_limit(max_calls=10, window_seconds=30)
def api_generate():
    data   = request.get_json() or {}
    nombre = (data.get("nombre", "") or "Jugador").strip()[:40]
    code   = (data.get("code", "")   or "").strip().upper()
    count  = min(int(data.get("count", 1)), 10)

    if not is_admin():
        with game_lock:
            if len(game.drawn) > 0:
                return jsonify({"error": "game_started"}), 403
        ok, err = validate_voucher_for_cartilla(code)
        if not ok:
            return jsonify({"error": err}), 403
        vinfo       = get_voucher_info(code)
        btype       = BINGO_TYPES.get(vinfo.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
        max_c       = btype.get("max_cartillas_per_voucher", 5)
        ya_tiene    = len(vinfo.get("cartillas", []))
        disponibles = max_c - ya_tiene
        if disponibles <= 0:
            return jsonify({"error": "max_cartillas_reached"}), 403
        count = min(count, disponibles)
    else:
        vinfo = get_voucher_info(code) if code else None

    session_id = (vinfo.get("session_id") if vinfo else None) or ""
    bingo_type = (vinfo.get("bingo_type") if vinfo else "1sol")
    por_admin  = is_admin()

    results = []
    for _ in range(count):
        grid = generate_cartilla_grid()
        c    = save_cartilla(nombre, grid, voucher_code=code,
                             session_id=session_id, bingo_type=bingo_type,
                             generada_por_admin=por_admin)
        results.append(c)
        if code:
            mark_voucher_cartilla(code, c["id"])

    return jsonify({"status": "ok", "cartillas": results})

@app.route("/api/cartilla/save_manual", methods=["POST"])
@rate_limit(max_calls=5, window_seconds=30)
def api_save_manual():
    data   = request.get_json() or {}
    nombre = (data.get("nombre", "") or "Jugador").strip()[:40]
    code   = (data.get("code", "")   or "").strip().upper()
    grid   = data.get("grid")
    if not is_admin():
        with game_lock:
            if len(game.drawn) > 0:
                return jsonify({"error": "game_started"}), 403
        ok, err = validate_voucher_for_cartilla(code)
        if not ok:
            return jsonify({"error": err}), 403
    if not grid or len(grid) != 3 or any(len(r) != 9 for r in grid):
        return jsonify({"error": "grid invalido"}), 400
    nums = [n for row in grid for n in row if n is not None]
    if len(nums) != 15:
        return jsonify({"error": "Se requieren 15 numeros"}), 400
    vinfo      = get_voucher_info(code) if code else None
    session_id = (vinfo.get("session_id") if vinfo else None) or ""
    bingo_type = (vinfo.get("bingo_type") if vinfo else "1sol")
    c = save_cartilla(nombre, grid, voucher_code=code,
                      session_id=session_id, bingo_type=bingo_type,
                      generada_por_admin=is_admin())
    if code:
        mark_voucher_cartilla(code, c["id"])
    return jsonify({"status": "ok", "cartilla": c})

@app.route("/api/cartilla/list")
def api_list_cartillas_legacy():
    session_id = request.args.get("session_id", "")
    cartillas  = load_all_cartillas(session_id or None)
    with game_lock:
        drawn2 = list(game.drawn)
    return jsonify({"cartillas": [
        {**check_winner(c["grid"], drawn2), "id": c["id"], "nombre": c["nombre"],
         "created": c.get("created", ""), "generada_por_admin": c.get("generada_por_admin", False)}
        for c in cartillas
    ]})

@app.route("/api/cartillas")
def api_list_cartillas():
    session_id = request.args.get("session_id", "")
    cartillas  = load_all_cartillas(session_id or None)
    with game_lock:
        drawn2 = list(game.drawn)
    return jsonify({"cartillas": [
        {**check_winner(c["grid"], drawn2), "id": c["id"], "nombre": c["nombre"],
         "created": c.get("created", "")}
        for c in cartillas
    ]})

@app.route("/api/cartilla/<cid>")
def api_get_cartilla(cid):
    c = load_cartilla(cid.upper())
    if not c:
        return jsonify({"error": "not found"}), 404
    with game_lock:
        drawn2 = list(game.drawn)
    result = check_winner(c["grid"], drawn2)
    result.update({
        "id": c["id"], "nombre": c["nombre"], "grid": c["grid"],
        "bingo_type": c.get("bingo_type", "1sol"),
        "session_id": c.get("session_id", ""),
        "voucher_code": c.get("voucher_code", ""),
        "generada_por_admin": c.get("generada_por_admin", False),
    })
    return jsonify(result)

@app.route("/api/cartilla/check_all")
def api_check_all():
    session_id = request.args.get("session_id", "")
    with game_lock:
        drawn2 = list(game.drawn)
    cartillas = load_all_cartillas(session_id or None)
    results   = []
    for c in cartillas:
        r = check_winner(c["grid"], drawn2)
        r["id"]     = c["id"]
        r["nombre"] = c["nombre"]
        results.append(r)
    return jsonify({"results": results, "drawn_count": len(drawn2)})

@app.route("/api/cartilla/<cid>/pdf")
def api_pdf(cid):
    c = load_cartilla(cid.upper())
    if not c: return jsonify({"error": "not found"}), 404
    with game_lock: drawn2 = list(game.drawn)
    buf  = cartilla_to_pdf(c, drawn2)
    name = f"cartilla_{cid}_{c['nombre'].replace(' ', '_')}.pdf"
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=name)

@app.route("/api/cartilla/<cid>/png")
def api_png(cid):
    c = load_cartilla(cid.upper())
    if not c: return jsonify({"error": "not found"}), 404
    with game_lock: drawn2 = list(game.drawn)
    buf  = cartilla_to_png(c, drawn2)
    name = f"cartilla_{cid}_{c['nombre'].replace(' ', '_')}.png"
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=name)

@app.route("/api/cartilla/<cid>/delete", methods=["DELETE"])
def api_delete_cartilla(cid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        c = db.query(Cartilla).filter_by(id=cid.upper()).first()
        if not c: return jsonify({"error": "not found"}), 404
        db.delete(c)
    return jsonify({"status": "ok"})

@app.route("/api/cartilla/delete_all", methods=["DELETE"])
def api_delete_all_cartillas():
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        count = db.query(Cartilla).delete()
    return jsonify({"status": "ok", "deleted": count})

# ─── Winner claim v7.2 (unchanged logic) ──────────────────────────────────────
@app.route("/api/winner/claim", methods=["POST"])
@rate_limit(max_calls=5, window_seconds=10)
def api_winner_claim():
    data = request.get_json() or {}
    cid  = (data.get("cid") or "").strip().upper()
    if not cid: return jsonify({"error": "missing_cid"}), 400
    c = load_cartilla(cid)
    if not c: return jsonify({"error": "not_found"}), 404

    # Fetch contact info from voucher (outside game lock)
    yape_plin    = ""
    winner_email = ""
    celular      = ""
    apellidos    = ""
    with db_session() as db:
        v_code = c.get("voucher_code", "")
        if v_code:
            v = db.query(Voucher).filter_by(code=v_code).first()
            if v:
                yape_plin    = v.yape_plin    or ""
                winner_email = v.email        or ""
                celular      = v.celular      or ""
                apellidos    = v.apellidos    or ""

    with game_lock:
        drawn2  = list(game.drawn)
        gid     = game.game_id
        claimed = game.claimed_winners
        chk     = check_winner(c["grid"], drawn2)

        # Validar que la cartilla pertenece a la sesión activa
        if c.get("session_id") and game.session_id and c["session_id"] != game.session_id:
            return jsonify({"error": "session_mismatch",
                            "message": "Esta cartilla no pertenece a la sesión activa."}), 403

        if not chk.get("bingo"):
            return jsonify({"ok": False, "error": "not_bingo",
                            "marked": chk.get("marked"), "total": chk.get("total")}), 400

        if cid in claimed:
            entry = next((w for w in game.winners_log if w["id"] == cid), None)
            return jsonify({"ok": True, "already": True, "game_id": gid, "winner": entry})

        claimed.add(cid)
        btype      = BINGO_TYPES.get(game.bingo_type, BINGO_TYPES["1sol"])
        n_winners  = len(claimed)
        prize_each = round(game.prize_pool / n_winners, 2)

        for prev in game.winners_log:
            if prev.get("game_id") == gid:
                prev["prize"]     = prize_each
                prev["n_winners"] = n_winners
                prev["split"]     = n_winners > 1

        winner = {
            "id":           cid,
            "nombre":       c.get("nombre"),
            "apellidos":    apellidos,
            "yape_plin":    yape_plin,
            "email":        winner_email,
            "celular":      celular,
            "claimed_at":   datetime.now().isoformat(),
            "drawn_count":  len(drawn2),
            "game_id":      gid,
            "prize":        prize_each,
            "n_winners":    n_winners,
            "split":        n_winners > 1,
            "bingo_type":   btype["id"],
            "bingo_nombre": btype["nombre"],
        }
        game.winners_log.append(winner)
        if n_winners >= game.winners_limit:
            game.paused = True
        game.save_to_db()

    # Send winner email notification (outside lock)
    if winner_email:
        asunto = f"Felicidades! Ganaste S/. {prize_each:.2f} — {btype['nombre']}"
        ok, err = enviar_email(winner_email, asunto, _email_ganador(winner, btype))
        if not ok:
            print(f"[WARN] Email ganador no enviado a {winner_email}: {err}")

    return jsonify({
        "ok": True, "already": False, "game_id": gid, "winner": winner,
        "split": n_winners > 1, "n_winners": n_winners, "prize_each": prize_each,
    })

@app.route("/api/winner/claim_linea", methods=["POST"])
@rate_limit(max_calls=5, window_seconds=10)
def api_winner_claim_linea():
    data = request.get_json() or {}
    cid  = (data.get("cid") or "").strip().upper()
    if not cid: return jsonify({"error": "missing_cid"}), 400
    c = load_cartilla(cid)
    if not c: return jsonify({"error": "not_found"}), 404

    # Obtener datos de contacto del voucher (fuera del lock)
    yape_plin    = ""
    winner_email = ""
    celular      = ""
    apellidos    = ""
    with db_session() as db:
        v_code = c.get("voucher_code", "")
        if v_code:
            v = db.query(Voucher).filter_by(code=v_code).first()
            if v:
                yape_plin    = v.yape_plin    or ""
                winner_email = v.email        or ""
                celular      = v.celular      or ""
                apellidos    = v.apellidos    or ""

    with game_lock:
        drawn2   = list(game.drawn)
        gid      = game.game_id
        linea_cl = game.linea_claimed
        chk      = check_winner(c["grid"], drawn2)

        # Validar que la cartilla pertenece a la sesión activa
        if c.get("session_id") and game.session_id and c["session_id"] != game.session_id:
            return jsonify({"error": "session_mismatch",
                            "message": "Esta cartilla no pertenece a la sesión activa."}), 403

        if not chk.get("linea"):
            return jsonify({"ok": False, "error": "not_linea"}), 400
        if cid in linea_cl:
            entry = next((w for w in game.linea_winners_log if w["id"] == cid), None)
            return jsonify({"ok": True, "already": True, "winner": entry})

        # Si ya hubo ganadores de línea en una bolilla anterior, cerrar nuevos reclamos
        current_drawn = len(drawn2)
        if game.linea_drawn_at is not None and current_drawn > game.linea_drawn_at:
            return jsonify({
                "ok": False, "error": "linea_closed",
                "message": "La línea ya fue ganada en una bolilla anterior. No se aceptan más reclamos.",
            }), 400

        # Registrar en qué bolilla se reclamó la primera línea
        if game.linea_drawn_at is None:
            game.linea_drawn_at = current_drawn

        linea_cl.add(cid)
        n_linea   = len(linea_cl)
        linea_each = round(game.linea_pool / n_linea, 2)

        for prev in game.linea_winners_log:
            if prev.get("game_id") == gid:
                prev["linea_prize"] = linea_each
                prev["n_winners"]   = n_linea
                prev["split"]       = n_linea > 1

        linea_winner = {
            "id":           cid,
            "nombre":       c.get("nombre"),
            "apellidos":    apellidos,
            "yape_plin":    yape_plin,
            "email":        winner_email,
            "celular":      celular,
            "claimed_at":   datetime.now().isoformat(),
            "drawn_count":  len(drawn2),
            "game_id":      gid,
            "linea_row":    chk.get("linea_row"),
            "linea_prize":  linea_each,
            "n_winners":    n_linea,
            "split":        n_linea > 1,
            "puede_reclamar_bingo": True,
        }
        game.linea_winners_log.append(linea_winner)
        game.save_to_db()

    return jsonify({
        "ok": True, "already": False, "game_id": gid, "winner": linea_winner,
        "linea_prize": linea_each, "n_winners": n_linea, "split": n_linea > 1,
        "row": chk.get("linea_row"), "nombre": c.get("nombre"),
        "puede_reclamar_bingo": True,
    })

# ─── Config API ───────────────────────────────────────────────────────────────
@app.route("/api/config")
def api_get_config():
    cfg = _load_config()
    cfg["email_configurado"] = email_configurado()
    return jsonify(cfg)

@app.route("/api/admin/config", methods=["GET"])
def api_admin_get_config():
    chk = admin_required()
    if chk: return chk
    cfg = _load_config()
    cfg["email_configurado"] = email_configurado()
    cfg["email_from"]        = EMAIL_FROM if email_configurado() else ""
    return jsonify(cfg)

@app.route("/api/admin/config", methods=["POST"])
def api_admin_save_config():
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}
    cfg  = _load_config()
    for field in ("nombre_organizador", "whatsapp", "facebook", "instagram",
                  "telefono_extra", "mensaje_contacto", "instrucciones_pago"):
        if field in data:
            cfg[field] = str(data[field])[:300]
    if "linea_premio_activo" in data:
        cfg["linea_premio_activo"] = bool(data["linea_premio_activo"])
    if "metodos_pago" in data and isinstance(data["metodos_pago"], list):
        cfg["metodos_pago"] = data["metodos_pago"]
    _save_config(cfg)
    return jsonify({"status": "ok", "config": cfg})

# ─── Stats ────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    with db_session() as db:
        total_v   = db.query(Voucher).count()
        paid_v    = db.query(Voucher).filter(
            Voucher.payment_status.in_(["approved", "manual_approved"])).count()
        pending_v = db.query(Voucher).filter_by(payment_status="pending_review").count()
        revenue   = sum(v.precio for v in db.query(Voucher).filter(
            Voucher.payment_status.in_(["approved", "manual_approved"])).all())
        total_s   = db.query(BingoSession).count()
        active_s  = db.query(BingoSession).filter_by(status="active").count()
        sched_s   = db.query(BingoSession).filter_by(status="scheduled").count()
    return jsonify({
        "vouchers":  {"total": total_v, "paid": paid_v, "pending": pending_v},
        "sessions":  {"total": total_s, "active": active_s, "upcoming": sched_s},
        "revenue":   round(revenue, 2),
    })

@app.route("/api/bingo_types")
def api_bingo_types():
    return jsonify({"bingo_types": list(BINGO_TYPES.values())})

# ─── Startup ──────────────────────────────────────────────────────────────────
def _startup():
    init_db()
    print("\n" + "=" * 58)
    print("  BINGO PRO WEB v8.0  (SQLite backend)")
    print("=" * 58)
    ip = get_local_ip()
    print(f"  Red WiFi  ->  http://{ip}:5000")
    print(f"  Admin     ->  http://{ip}:5000/admin")
    print(f"  Pagos     ->  http://{ip}:5000/admin/payments")
    print(f"  DB        ->  {BASE_DIR}/bingo.db")
    print("=" * 58)
    if not ADMIN_USER or not ADMIN_PASS:
        print("\n  ERROR: Configura ADMIN_USER y ADMIN_PASS en .env")
    if not email_configurado():
        print("  ADVERTENCIA: EMAIL_FROM y EMAIL_PASS no configurados - emails desactivados")
    print()

# ─── CSRF ────────────────────────────────────────────────────────────────────
@app.route("/api/csrf-token")
def api_csrf_token():
    return jsonify({"token": get_csrf_token()})

# ─── 2FA Setup ────────────────────────────────────────────────────────────────
@app.route("/admin/setup_2fa")
def admin_setup_2fa():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_setup_2fa.html")

@app.route("/api/admin/2fa/qr")
def api_admin_2fa_qr():
    chk = admin_required()
    if chk: return chk
    if not TOTP_SECRET:
        return jsonify({"error": "2fa_not_configured"}), 400
    uri = totp_provisioning_uri(TOTP_SECRET, ADMIN_USER, "Bingo Pro")
    return jsonify({"uri": uri, "secret": TOTP_SECRET})

@app.route("/api/admin/2fa/verify", methods=["POST"])
def api_admin_2fa_verify():
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}
    ok   = totp_verify(TOTP_SECRET, data.get("code", "")) if TOTP_SECRET else False
    return jsonify({"valid": ok})

# ─── Security stats ───────────────────────────────────────────────────────────
@app.route("/api/admin/security")
def api_security_stats():
    chk = admin_required()
    if chk: return chk
    return jsonify(get_security_stats())

# ─── Auto-draw ────────────────────────────────────────────────────────────────
@app.route("/api/admin/session/<sid>/autodraw/start", methods=["POST"])
def api_autodraw_start(sid):
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}
    s    = get_session(sid)
    if not s: return jsonify({"error": "not_found"}), 404
    if s.get("status") != "active":
        return jsonify({"error": "session_not_active"}), 400
    interval      = int(data.get("interval", AUTO_DRAW_INTERVAL))
    winners_limit = int(data.get("winners_limit", 1))
    with game_lock:
        game.winners_limit = winners_limit
    autodraw.configure_session(
        sid,
        interval      = interval,
        voice         = data.get("voice", AUTO_DRAW_VOICE),
        auto_finish   = bool(data.get("auto_finish", True)),
        winners_limit = winners_limit,
    )
    return jsonify({"status": "ok", "message": f"Sorteo automático: bolilla cada {interval}s"})

@app.route("/api/admin/session/<sid>/autodraw/stop", methods=["POST"])
def api_autodraw_stop(sid):
    chk = admin_required()
    if chk: return chk
    autodraw.stop_session(sid)
    return jsonify({"status": "ok", "message": "Sorteo automático detenido."})

@app.route("/api/admin/autodraw/status")
def api_autodraw_status():
    chk = admin_required()
    if chk: return chk
    return jsonify(autodraw.get_status())

@app.route("/api/admin/autodraw/audit")
def api_autodraw_audit():
    chk = admin_required()
    if chk: return chk
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify({"log": autodraw.get_audit_log(limit)})

@app.route("/api/admin/session/<sid>/auto_start", methods=["POST"])
def api_session_auto_start(sid):
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}
    with sessions_lock:
        ss = _load_sessions()
        for s in ss:
            if s.get("id") == sid:
                s["auto_start"] = bool(data.get("auto_start", True))
                s["auto_draw_config"] = {
                    "interval":      int(data.get("interval", AUTO_DRAW_INTERVAL)),
                    "voice":         data.get("voice", AUTO_DRAW_VOICE),
                    "auto_finish":   bool(data.get("auto_finish", True)),
                    "winners_limit": int(data.get("winners_limit", 1)),
                }
                _save_sessions(ss)
                return jsonify({"status": "ok", "session": s})
    return jsonify({"error": "not_found"}), 404

# Ejecutar al importar (gunicorn) y también al correr directo
_startup()

if __name__ == "__main__":
    ip = get_local_ip()

    # Inicializar autodraw
    autodraw.init(
        game_obj              = game,
        game_lock_obj         = game_lock,
        load_all_cartillas_fn = load_all_cartillas,
        check_winner_fn       = check_winner,
        load_sessions_fn      = _load_sessions,
        save_sessions_fn      = _save_sessions,
        sessions_lock_obj     = sessions_lock,
        load_vouchers_fn      = _load_vouchers,
        vouchers_lock_obj     = vouchers_lock,
        save_sessions_ref     = _save_sessions,
        enviar_email_fn       = enviar_email,
        email_templates       = {},
        bingo_types_dict      = BINGO_TYPES,
        cartillas_dir_path    = CARTILLAS_DIR,
    )
    autodraw.start_scheduler()

    print("\n" + "="*62)
    print("  BINGO PRO WEB v9.0  Security + Auto-Draw")
    print("="*62)
    print(f"  Red WiFi  ->  http://{ip}:5000")
    print(f"  Admin     ->  http://{ip}:5000/admin")
    print(f"  2FA Setup ->  http://{ip}:5000/admin/setup_2fa")
    print("="*62)
    if TOTP_SECRET:
        print("  2FA: ACTIVADO")
    else:
        print("  ADVERTENCIA: 2FA desactivado - agrega TOTP_SECRET al .env")
    print(f"  Auto-draw: cada {AUTO_DRAW_INTERVAL}s")
    if not email_configurado():
        print("  ADVERTENCIA: EMAIL_FROM y EMAIL_PASS no configurados")
    print()
    app.run(host="0.0.0.0", port=5000, debug=False)