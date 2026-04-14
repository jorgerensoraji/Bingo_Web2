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

import asyncio, base64, hashlib, json, os, random, re, socket, tempfile
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
from zoneinfo import ZoneInfo

_PERU_TZ = ZoneInfo("America/Lima")

def now_peru() -> datetime:
    """Current naive datetime in Peru time (UTC-5, no DST)."""
    return datetime.now(_PERU_TZ).replace(tzinfo=None)
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
from models import Voucher, Session as BingoSession, Cartilla, Config, BingoTypeDB, Contact, Reimbursement, Winner, _winner_prize

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

# Twilio WhatsApp
TWILIO_SID        = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN      = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM    = os.environ.get("TWILIO_WHATSAPP_FROM", "")  # e.g. whatsapp:+14155238886
TWILIO_COUNTRY    = os.environ.get("TWILIO_DEFAULT_COUNTRY", "+51")  # default country code
ADMIN_WHATSAPP    = os.environ.get("ADMIN_WHATSAPP", "")  # admin's personal WhatsApp e.g. +51987654321

if not ADMIN_USER or not ADMIN_PASS:
    print("\nERROR: ADMIN_USER y ADMIN_PASS no configurados.\n")

app.config["SESSION_PERMANENT"]       = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

def is_admin() -> bool:
    return bool(session.get("is_admin"))

# ─── Quick-action token helpers (HMAC, no DB needed) ──────────────────────────
import hmac as _hmac, hashlib as _hashlib

def _quick_token(action: str, code: str) -> str:
    """Generate a secure token for one-click approve/reject links."""
    msg = f"{action}:{code}".encode()
    return _hmac.new(_raw_key.encode(), msg, _hashlib.sha256).hexdigest()[:32]

def _verify_quick_token(action: str, code: str, token: str) -> bool:
    expected = _quick_token(action, code)
    return _hmac.compare_digest(expected, (token or ""))

def is_setup_pending() -> bool:
    """True when user passed password check but hasn't enrolled 2FA yet."""
    return bool(session.get("totp_setup_pending"))

def admin_required():
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401
    return None

def get_totp_enrolled() -> bool:
    try:
        with db_session() as db:
            row = db.query(Config).filter_by(key="totp_enrolled").first()
            return bool(row and row.value == "true")
    except Exception:
        return False

def set_totp_enrolled(val: bool):
    try:
        with db_session() as db:
            row = db.query(Config).filter_by(key="totp_enrolled").first()
            if row:
                row.value = "true" if val else "false"
            else:
                db.add(Config(key="totp_enrolled", value="true" if val else "false"))
            db.commit()
    except Exception:
        pass

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

# ─── Configuración de Email (Brevo HTTP API — puerto 443, nunca bloqueado) ────
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "")
EMAIL_PASS    = os.environ.get("EMAIL_PASS", "")   # Brevo API Key v3 (xkeysib-...)
EMAIL_NOMBRE  = os.environ.get("EMAIL_NOMBRE", "Bingo Pro")
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

def email_configurado() -> bool:
    return bool(EMAIL_FROM and EMAIL_PASS)

def hash_pin(pin: str) -> str:
    """SHA-256 del PIN. Nunca guardamos el PIN en texto claro."""
    return hashlib.sha256(pin.strip().encode()).hexdigest()

# ─── Chat en memoria ──────────────────────────────────────────────────────────
import threading as _threading
_chat_lock  = _threading.Lock()
_chat_msgs  = []          # lista de dicts: {id, name, msg, ts, color}
_chat_next_id = 1
_CHAT_MAX   = 150         # máximo mensajes en memoria

# ── In-memory email log (last 100 events) ────────────────
_email_log       = []
_email_log_lock  = threading.Lock()
_EMAIL_LOG_MAX   = 100

_EMAIL_LOG_FILE = os.path.join(os.path.dirname(__file__), "email_log.txt")

def _log_email_event(level: str, msg: str) -> None:
    ts    = now_peru().isoformat(timespec="seconds")
    entry = {"ts": ts, "level": level, "msg": msg}
    line  = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    with _email_log_lock:
        _email_log.append(entry)
        if len(_email_log) > _EMAIL_LOG_MAX:
            _email_log.pop(0)
    try:
        with open(_EMAIL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
_CHAT_COLORS = [
    "#00e5b4","#f6c343","#58d68d","#5dade2",
    "#a569bd","#ff8c42","#48c9b0","#f1948a",
]

def _chat_color(name: str) -> str:
    idx = sum(ord(c) for c in name) % len(_CHAT_COLORS)
    return _CHAT_COLORS[idx]

def enviar_email(destinatario: str, asunto: str, cuerpo_html: str, attachments: list = None) -> tuple:
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
        if attachments:
            payload["attachment"] = attachments
        headers = {
            "api-key":      EMAIL_PASS,
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }
        resp = http_requests.post(BREVO_API_URL, json=payload, headers=headers, timeout=20)
        if resp.status_code in (200, 201):
            return True, ""
        try:
            err_body = resp.json()
            err_msg  = err_body.get("message", resp.text[:200])
        except Exception:
            err_msg = resp.text[:200]
        return False, f"Brevo error {resp.status_code}: {err_msg}"
    except http_requests.exceptions.Timeout:
        return False, "Timeout conectando con Brevo."
    except Exception as e:
        return False, f"Error: {e}"

# ─── Email templates (unchanged from v7.2) ───────────────────────────────────
def _email_codigo_voucher(voucher_dict: dict, url_base: str, plain_pin: str = "") -> str:
    bt     = BINGO_TYPES.get(voucher_dict.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    code   = voucher_dict.get("code", "")
    nombre = voucher_dict.get("nombres", "Jugador")
    precio = bt["precio"]
    url_cartillas = f"{url_base}/cartillas?code={code}"
    url_juego     = f"{url_base}/"

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:28px">
    <h1 style="font-size:2rem;color:#00e5b4;letter-spacing:3px;margin:0">🎱 BINGO PRO</h1>
    <p style="color:#4a6b85;margin:4px 0 0">Tu código de participación</p>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:14px;padding:24px;margin-bottom:20px">
    <p style="margin:0 0 8px;color:#4a6b85;font-size:.85rem">Hola, <strong style="color:#ddeeff">{nombre}</strong></p>
    <p style="margin:0 0 20px;color:#ddeeff">¡Tu pago fue confirmado! Aquí están tus datos de acceso:</p>
    <div style="background:#111f2e;border:2px solid #00e5b4;border-radius:12px;padding:20px;text-align:center;margin-bottom:20px">
      <div style="font-size:.75rem;color:#4a6b85;letter-spacing:3px;text-transform:uppercase;margin-bottom:8px">Tu código de voucher</div>
      <div style="font-family:'Courier New',monospace;font-size:2.4rem;font-weight:900;color:#00e5b4;letter-spacing:8px">{code}</div>
      <div style="font-size:.78rem;color:#4a6b85;margin-top:8px">{bt['emoji']} {bt['nombre']} — S/. {precio:.2f}</div>
    </div>
    <div style="background:#0a1520;border-radius:10px;padding:14px;font-size:.85rem;color:#4a6b85;margin-bottom:16px">
      <strong style="color:#ddeeff">¿Cómo participar?</strong><br><br>
      1. <a href="{url_cartillas}" style="color:#00e5b4;font-weight:700">Haz clic aquí para generar tu cartilla →</a> (tu código <strong style="color:#00e5b4">{code}</strong> se cargará automáticamente)<br><br>
      2. El día del juego entra a <a href="{url_juego}" style="color:#00e5b4">{url_juego}</a><br><br>
      3. Si juegas desde otro dispositivo, usa tu código <strong style="color:#00e5b4">{code}</strong> para cargar tus cartillas<br><br>
      4. ¡Disfruta el juego y que ganes! 🎉
    </div>
    <div style="background:#0d1e14;border:1px solid rgba(0,229,180,.2);border-radius:10px;padding:14px;font-size:.82rem;margin-bottom:20px">
      <strong style="color:#00e5b4;display:block;margin-bottom:8px">💰 Distribución de premios</strong>
      <table style="width:100%;border-collapse:collapse;color:#4a6b85">
        <tr><td style="padding:3px 0">⭐ LÍNEA</td><td style="text-align:right;color:#f6c343">{round(bt.get('linea_pct',0.04)*100,0):.0f}% del pozo</td></tr>
        <tr><td style="padding:3px 0">🔷 Formar Letra U</td><td style="text-align:right;color:#3b82f6">{round(bt.get('u_pct',0.13)*100,0):.0f}% del pozo</td></tr>
        <tr><td style="padding:3px 0">⭕ Formar Letra O</td><td style="text-align:right;color:#ec4899">{round(bt.get('o_pct',0.15)*100,0):.0f}% del pozo</td></tr>
        <tr style="border-top:1px solid rgba(255,255,255,.08)"><td style="padding:4px 0;color:#ddeeff;font-weight:700">🎉 Cartón lleno o Bingo</td><td style="text-align:right;color:#00e5b4;font-weight:700">{round(bt.get('prize_pct',0.55)*100,0):.0f}% mínimo</td></tr>
      </table>
      <div style="margin-top:8px;font-size:.76rem;color:#3a5568;border-top:1px solid rgba(255,255,255,.06);padding-top:8px">
        💡 Si nadie gana U o O, esos premios se acumulan al BINGO. <strong style="color:#ddeeff">El 100% vuelve a los jugadores.</strong>
      </div>
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin:0">Bingo Pro Web v9.0 · {EMAIL_NOMBRE}</p>
  <p style="text-align:center;color:#1a3148;font-size:.70rem;margin:4px 0 0">Por favor no respondas a este correo — esta dirección no está monitoreada.</p>
</div></body></html>"""

def _email_pago_recibido(voucher_dict: dict, plain_pin: str = "") -> str:
    bt      = BINGO_TYPES.get(voucher_dict.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    nombre  = voucher_dict.get("nombres", "Jugador")
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:28px">
    <h1 style="font-size:2rem;color:#00e5b4;letter-spacing:3px;margin:0">🎱 BINGO PRO</h1>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:14px;padding:24px">
    <p style="margin:0 0 12px">Hola <strong style="color:#ddeeff">{nombre}</strong>,</p>
    <p style="color:#ddeeff;margin:0 0 16px">Recibimos tu solicitud para
      <strong style="color:{bt['color']}">{bt['emoji']} {bt['nombre']}</strong> (S/. {bt['precio']:.2f}).
    </p>
    <div style="background:#111f2e;border-radius:10px;padding:14px;font-size:.85rem;color:#4a6b85">
      Estamos verificando tu pago. En cuanto sea aprobado recibirás otro email con tu código de cartilla.<br><br>
      <strong style="color:#f6c343">Tiempo estimado: menos de 30 minutos.</strong>
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin-top:20px">Bingo Pro Web v9.0 · {EMAIL_NOMBRE}</p>
  <p style="text-align:center;color:#1a3148;font-size:.70rem;margin:4px 0 0">Por favor no respondas a este correo — esta dirección no está monitoreada.</p>
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
  <p style="text-align:center;color:#1a3148;font-size:.70rem;margin:4px 0 0">Por favor no respondas a este correo — esta dirección no está monitoreada.</p>
</div></body></html>"""

def _email_sesion_cancelada(voucher_dict: dict, session_nombre: str, amount: float) -> str:
    nombre = voucher_dict.get("nombres", "Jugador")
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:28px">
    <h1 style="font-size:2rem;color:#00e5b4;letter-spacing:3px;margin:0">🎱 BINGO PRO</h1>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:14px;padding:24px">
    <p style="margin:0 0 12px">Hola <strong style="color:#ddeeff">{nombre}</strong>,</p>
    <p style="color:#ddeeff;margin:0 0 16px">
      Lamentamos informarte que la sesión <strong style="color:#f6c343">{session_nombre}</strong> ha sido cancelada.
    </p>
    <div style="background:#111f2e;border-radius:10px;padding:14px;font-size:.85rem;color:#ddeeff;margin-bottom:16px">
      Tu pago de <strong style="color:#00e5b4">S/. {amount:.2f}</strong> será reembolsado a tu número Yape/Plin:
      <br><strong style="color:#f6c343">{voucher_dict.get('yape_plin','')}</strong>
    </div>
    <div style="background:rgba(231,76,60,.1);border:1px solid rgba(231,76,60,.25);border-radius:10px;
                padding:12px;font-size:.82rem;color:#e74c3c">
      Si no recibes el reembolso en 48 horas, contáctanos.
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin-top:20px">Bingo Pro Web v9.0 · {EMAIL_NOMBRE}</p>
  <p style="text-align:center;color:#1a3148;font-size:.70rem;margin:4px 0 0">Por favor no respondas a este correo — esta dirección no está monitoreada.</p>
</div></body></html>"""

def _email_cartilla_generada(voucher_dict: dict, cartillas: list, url_base: str) -> str:
    nombre   = voucher_dict.get("nombres", "Jugador")
    bt       = BINGO_TYPES.get(voucher_dict.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    url_juego = f"{url_base}/"
    ids_html = "".join(
        f'<div style="font-family:\'Courier New\',monospace;font-size:1.4rem;font-weight:900;'
        f'color:#00e5b4;letter-spacing:6px;margin:4px 0">{c["id"]}</div>'
        for c in cartillas
    )
    count = len(cartillas)
    plural = "s" if count > 1 else ""
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:28px">
    <h1 style="font-size:2rem;color:#00e5b4;letter-spacing:3px;margin:0">🎱 BINGO PRO</h1>
    <p style="color:#6a9ab8;margin:4px 0 0">Tu{plural} cartilla{plural} {'están listas' if count>1 else 'está lista'}</p>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:14px;padding:24px;margin-bottom:20px">
    <p style="margin:0 0 8px;color:#6a9ab8;font-size:.85rem">Hola, <strong style="color:#ddeeff">{nombre}</strong></p>
    <p style="margin:0 0 20px;color:#ddeeff">
      {'Tu cartilla ha sido' if count==1 else f'Tus {count} cartillas han sido'} generada{plural} exitosamente.
      {'La encontrarás adjunta' if count==1 else 'Las encontrarás adjuntas'} a este correo.
    </p>
    <div style="background:#111f2e;border:2px solid #00e5b4;border-radius:12px;padding:20px;text-align:center;margin-bottom:20px">
      <div style="font-size:.75rem;color:#6a9ab8;letter-spacing:3px;text-transform:uppercase;margin-bottom:10px">
        ID{plural} de tu{plural} cartilla{plural}
      </div>
      {ids_html}
      <div style="font-size:.78rem;color:#6a9ab8;margin-top:10px">{bt['emoji']} {bt['nombre']}</div>
    </div>
    <div style="background:#0a1520;border-radius:10px;padding:14px;font-size:.85rem;color:#6a9ab8;margin-bottom:20px">
      <strong style="color:#ddeeff">¿Cómo jugar?</strong><br><br>
      1. El día del bingo entra al juego en: <a href="{url_juego}" style="color:#00e5b4">{url_juego}</a><br><br>
      2. Si juegas desde otro dispositivo o navegador, en la página del juego busca la sección
         <strong style="color:#00e5b4">▼ ¿Ya tienes tu código?</strong>, ingresa el
         <strong style="color:#00e5b4">ID de tu cartilla</strong> (los {'8 caracteres' if count==1 else '8 caracteres de cada ID'} de arriba) y haz clic en <strong style="color:#ddeeff">→ Verificar</strong>.<br><br>
      3. ¡Buena suerte! 🎉
    </div>
    <div style="text-align:center">
      <a href="{url_juego}" style="display:inline-block;padding:14px 28px;background:#00e5b4;color:#041015;
         border-radius:10px;text-decoration:none;font-weight:900;font-size:.95rem">
        🎮 Ir al Juego
      </a>
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin:0">Bingo Pro Web · {EMAIL_NOMBRE}</p>
  <p style="text-align:center;color:#1a3148;font-size:.70rem;margin:4px 0 0">Por favor no respondas a este correo — esta dirección no está monitoreada.</p>
</div></body></html>"""

def _send_cartilla_email_async(vinfo: dict, cartillas: list, url_base: str) -> None:
    """Fire-and-forget: send cartilla PNG(s) to player email in a background thread."""
    def _do():
        try:
            attachments = []
            for c in cartillas:
                buf = cartilla_to_png(c)
                b64 = base64.b64encode(buf.read()).decode()
                attachments.append({"content": b64, "name": f"cartilla_{c['id']}.png"})
            html = _email_cartilla_generada(vinfo, cartillas, url_base)
            count = len(cartillas)
            ids   = ", ".join(c["id"] for c in cartillas)
            subject = f"🎴 Tu{'s' if count>1 else ''} cartilla{'s' if count>1 else ''} de Bingo {'están listas' if count>1 else 'está lista'}"
            ok, err = enviar_email(vinfo["email"], subject, html, attachments)
            if ok:
                _log_email_event("OK", f"Cartilla(s) [{ids}] enviada(s) a {vinfo['email']}")
            else:
                _log_email_event("ERROR", f"Fallo enviando cartilla(s) [{ids}] a {vinfo['email']}: {err}")
        except Exception as e:
            import traceback
            _log_email_event("EXCEPTION", f"{e} | {traceback.format_exc()}")
    threading.Thread(target=_do, daemon=True).start()

def _email_resumen_bingo(nombre: str, bingo_nombre: str, drawn: list,
                         cartilla_result: dict, url_base: str) -> str:
    """Email sent to every player when a bingo session finishes."""
    drawn_set = set(drawn)
    cols = [("B", range(1,16)), ("I", range(16,31)), ("N", range(31,46)),
            ("G", range(46,61)), ("O", range(61,76))]
    col_colors = ["#3b82f6","#f59e0b","#ef4444","#10b981","#a855f7"]

    balls_html = ""
    for (letter, rng), color in zip(cols, col_colors):
        balls_html += f'<div style="margin-bottom:10px">'
        balls_html += (f'<div style="font-weight:900;color:{color};font-size:.8rem;'
                       f'letter-spacing:2px;margin-bottom:5px">{letter}</div>')
        balls_html += '<div style="display:flex;flex-wrap:wrap;gap:4px">'
        for n in rng:
            hit = n in drawn_set
            bg = color if hit else "#111f2e"
            txt = "#fff" if hit else "#3a5570"
            balls_html += (f'<span style="display:inline-flex;align-items:center;justify-content:center;'
                           f'width:28px;height:28px;border-radius:50%;background:{bg};color:{txt};'
                           f'font-size:.72rem;font-weight:700">{n}</span>')
        balls_html += '</div></div>'

    won = cartilla_result.get("bingo")
    linea = cartilla_result.get("linea")
    marked = cartilla_result.get("marked", 0)
    if won:
        result_banner = ('<div style="background:rgba(0,229,180,.15);border:2px solid #00e5b4;'
                         'border-radius:12px;padding:14px;text-align:center;margin-bottom:16px">'
                         '<div style="font-size:1.6rem;font-weight:900;color:#00e5b4">🎉 ¡BINGO! ¡Ganaste!</div>'
                         '</div>')
    elif linea:
        result_banner = ('<div style="background:rgba(246,195,67,.12);border:2px solid #f6c343;'
                         'border-radius:12px;padding:14px;text-align:center;margin-bottom:16px">'
                         '<div style="font-size:1.4rem;font-weight:900;color:#f6c343">⭐ ¡Completaste la Letra I!</div>'
                         '</div>')
    else:
        result_banner = ('<div style="background:rgba(26,49,72,.5);border:1px solid #1a3148;'
                         'border-radius:12px;padding:14px;text-align:center;margin-bottom:16px">'
                         f'<div style="color:#8dbdd6">Tuviste <strong style="color:#ddeeff">{marked}</strong>'
                         ' número(s) marcados. ¡Suerte en el próximo! 🍀</div>'
                         '</div>')

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:540px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <h1 style="font-size:2rem;color:#00e5b4;letter-spacing:3px;margin:0">🎱 BINGO PRO</h1>
    <p style="color:#6a9ab8;margin:4px 0 0">Resumen de sesión — {bingo_nombre}</p>
  </div>
  <div style="background:#0d1825;border:1px solid #1a3148;border-radius:14px;padding:24px;margin-bottom:20px">
    <p style="margin:0 0 16px;color:#6a9ab8;font-size:.85rem">
      Hola, <strong style="color:#ddeeff">{nombre}</strong> — el bingo ha terminado.
      Adjunto encontrarás tu cartilla con los números marcados.
    </p>
    {result_banner}
    <div style="background:#111f2e;border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="font-size:.65rem;color:#6a9ab8;letter-spacing:3px;text-transform:uppercase;margin-bottom:12px">
        Bolillas sorteadas ({len(drawn)} en total)
      </div>
      {balls_html}
    </div>
    <div style="text-align:center">
      <a href="{url_base}/" style="display:inline-block;padding:12px 24px;background:#00e5b4;
         color:#041015;border-radius:10px;text-decoration:none;font-weight:900;font-size:.9rem">
        🎮 Ver próximos Bingos
      </a>
    </div>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin:0">Bingo Pro Web · {EMAIL_NOMBRE}</p>
  <p style="text-align:center;color:#1a3148;font-size:.70rem;margin:4px 0 0">Por favor no respondas a este correo — esta dirección no está monitoreada.</p>
</div></body></html>"""


def _send_fin_bingo_emails_async(sid: str, drawn: list, url_base: str) -> None:
    """Fire-and-forget: after session finishes, email every player their cartilla + drawn balls."""
    def _do():
        try:
            cartillas = load_all_cartillas(session_id=sid)
            if not cartillas:
                return
            # Build voucher_code → email/nombre map
            codes = list({c.get("voucher_code","") for c in cartillas if c.get("voucher_code")})
            voucher_map = {}
            if codes:
                with db_session() as db:
                    vs = db.query(Voucher).filter(Voucher.code.in_(codes)).all()
                    for v in vs:
                        voucher_map[v.code] = v.to_dict()
            # Group cartillas by player (voucher_code)
            by_voucher = {}
            no_voucher = []
            for c in cartillas:
                vc = c.get("voucher_code","")
                if vc and vc in voucher_map:
                    by_voucher.setdefault(vc, []).append(c)
                else:
                    no_voucher.append(c)

            sent_count = 0
            for vc, clist in by_voucher.items():
                vinfo = voucher_map[vc]
                email = vinfo.get("email","").strip()
                if not email:
                    continue
                nombre = vinfo.get("nombres","Jugador")
                bingo_nombre = vinfo.get("bingo_nombre","Bingo")
                attachments = []
                # Use best cartilla result for banner (bingo > linea > nothing)
                best_result = None
                for c_chk in clist:
                    r = check_winner(c_chk["grid"], drawn)
                    if r.get("bingo"):
                        best_result = r
                        break
                    if best_result is None or (r.get("linea") and not best_result.get("linea")):
                        best_result = r
                first_result = best_result or check_winner(clist[0]["grid"], drawn)
                for c in clist:
                    buf = cartilla_to_png(c, drawn)
                    b64 = base64.b64encode(buf.read()).decode()
                    attachments.append({"content": b64, "name": f"cartilla_{c['id']}.png"})
                html = _email_resumen_bingo(nombre, bingo_nombre, drawn, first_result, url_base)
                subject = f"🎱 Resumen de tu Bingo — {bingo_nombre}"
                ok, err = enviar_email(email, subject, html, attachments)
                if ok:
                    sent_count += 1
                    _log_email_event("OK", f"Resumen bingo [{sid}] enviado a {email}")
                else:
                    _log_email_event("ERROR", f"Fallo resumen bingo [{sid}] a {email}: {err}")
            _log_email_event("OK", f"Resúmenes bingo [{sid}]: {sent_count} emails enviados, {len(no_voucher)} cartillas sin email")
        except Exception as e:
            import traceback
            _log_email_event("EXCEPTION", f"_send_fin_bingo_emails_async: {e} | {traceback.format_exc()}")
    threading.Thread(target=_do, daemon=True).start()


def _email_ganador(winner: dict, btype: dict, pattern: str = "bingo") -> str:
    nombre     = winner.get("nombre", "Jugador")
    # Resolve prize amount by pattern
    if pattern == "u":
        prize = winner.get("u_prize", winner.get("prize", 0))
        pattern_label = "🔷 Formar Letra U"
    elif pattern == "o":
        prize = winner.get("o_prize", winner.get("prize", 0))
        pattern_label = "⭕ Formar Letra O"
    elif pattern == "linea":
        prize = winner.get("linea_prize", winner.get("prize", 0))
        pattern_label = "⭐ Formar Letra I"
    else:
        prize = winner.get("prize", 0)
        pattern_label = "🎉 Bingo / Apagón"
    yape_plin  = winner.get("yape_plin", "")
    drawn      = winner.get("drawn_count", 0)
    split      = winner.get("split", False)
    n_winners  = winner.get("n_winners", 1)
    merged_o   = float(winner.get("merged_o", 0) or 0)
    merged_u   = float(winner.get("merged_u", 0) or 0)
    split_note = (f"<div style='background:#2a1f00;border:1px solid #f6c343;border-radius:8px;"
                  f"padding:10px 14px;margin:0 0 14px;font-size:.87rem;color:#f6c343;text-align:center'>"
                  f"⚠️ Premio dividido: {n_winners} ganadores ganaron al mismo tiempo.<br>"
                  f"<strong>El pozo se dividio en partes iguales — S/. {prize:.2f} c/u.</strong></div>") if split else ""
    merge_rows = ""
    if pattern == "bingo" and (merged_o > 0 or merged_u > 0):
        base = prize - merged_o - merged_u
        rows = [f"<tr><td style='padding:4px 8px;color:#4a6b85'>Bingo / Apagón base</td><td style='padding:4px 8px;color:#ddeeff;text-align:right'>S/. {base:.2f}</td></tr>"]
        if merged_u > 0:
            rows.append(f"<tr><td style='padding:4px 8px;color:#3b82f6'>+ Formar Letra U (nadie ganó)</td><td style='padding:4px 8px;color:#3b82f6;text-align:right'>S/. {merged_u:.2f}</td></tr>")
        if merged_o > 0:
            rows.append(f"<tr><td style='padding:4px 8px;color:#ec4899'>+ Formar Letra O (nadie ganó)</td><td style='padding:4px 8px;color:#ec4899;text-align:right'>S/. {merged_o:.2f}</td></tr>")
        merge_rows = (f"<div style='background:#0a1520;border:1px solid rgba(0,229,180,.2);border-radius:8px;padding:10px;margin:12px 0;font-size:.82rem'>"
                      f"<div style='color:#00e5b4;font-weight:700;margin-bottom:6px'>🎁 Premio acumulado</div>"
                      f"<table style='width:100%;border-collapse:collapse'>{''.join(rows)}"
                      f"<tr style='border-top:1px solid rgba(255,255,255,.1)'><td style='padding:6px 8px;color:#ddeeff;font-weight:700'>Total recibido</td>"
                      f"<td style='padding:6px 8px;color:#00e5b4;font-weight:900;text-align:right'>S/. {prize:.2f}</td></tr>"
                      f"</table></div>")
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
    <p style="color:#00e5b4;font-weight:700;font-size:1rem;margin:4px 0 0">{pattern_label}</p>
    <p style="color:#4a6b85;margin:4px 0 0">{btype.get('nombre','Bingo Pro')}</p>
  </div>
  <div style="background:#0d1825;border:2px solid #00e5b4;border-radius:14px;padding:24px">
    <p style="margin:0 0 12px">Felicidades <strong style="color:#ddeeff">{nombre}</strong>!</p>
    {split_note}
    <div style="text-align:center;margin:16px 0">
      <div style="font-size:.8rem;color:#4a6b85;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px">Tu premio</div>
      <div style="font-size:2.8rem;font-weight:900;color:#00e5b4">S/. {prize:.2f}</div>
    </div>
    {merge_rows}
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
  <p style="text-align:center;color:#1a3148;font-size:.70rem;margin:4px 0 0">Por favor no respondas a este correo — esta dirección no está monitoreada.</p>
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

# ─── Tipos de Bingo — loaded from DB, seeded on first run ────────────────────
BINGO_TYPES: dict = {}   # populated by _reload_bingo_types() inside _startup()

# Default seed data — only inserted once when the DB is brand new
_SEED_BINGO_TYPES = {
    "1sol": {
        "id": "1sol", "nombre": "Bingo 1 Sol", "precio": 1.00,
        "color": "#f472b6", "emoji": "🎯", "descripcion": "Bingo B-I-N-G-O · 1 Sol",
        "prize_pct": 0.55, "linea_pct": 0.04, "u_pct": 0.13, "o_pct": 0.15,
        "house_pct": 0.13, "max_cartillas": 1, "max_cartillas_per_voucher": 1,
        "balls": 75, "grid_type": "75", "is_active": True, "is_special": False,
        "special_label": "", "sort_order": 1,
    },
    "5soles": {
        "id": "5soles", "nombre": "Bingo 5 Soles", "precio": 5.00,
        "color": "#a855f7", "emoji": "🎯", "descripcion": "Bingo B-I-N-G-O · 5 Soles",
        "prize_pct": 0.55, "linea_pct": 0.04, "u_pct": 0.13, "o_pct": 0.15,
        "house_pct": 0.13, "max_cartillas": 1, "max_cartillas_per_voucher": 1,
        "balls": 75, "grid_type": "75", "is_active": True, "is_special": False,
        "special_label": "", "sort_order": 2,
    },
    "10soles": {
        "id": "10soles", "nombre": "Bingo 10 Soles", "precio": 10.00,
        "color": "#818cf8", "emoji": "🎯", "descripcion": "Bingo B-I-N-G-O · 10 Soles",
        "prize_pct": 0.55, "linea_pct": 0.04, "u_pct": 0.13, "o_pct": 0.15,
        "house_pct": 0.13, "max_cartillas": 1, "max_cartillas_per_voucher": 1,
        "balls": 75, "grid_type": "75", "is_active": True, "is_special": False,
        "special_label": "", "sort_order": 3,
    },
}

def _seed_bingo_types() -> None:
    """Insert default bingo types on first run (skips if table already has rows)."""
    with db_session() as db:
        if db.query(BingoTypeDB).count() > 0:
            return
        for i, (tid, bt) in enumerate(_SEED_BINGO_TYPES.items(), 1):
            db.add(BingoTypeDB(
                id=tid, nombre=bt["nombre"], precio=bt["precio"],
                color=bt["color"], emoji=bt["emoji"], descripcion=bt["descripcion"],
                prize_pct=bt["prize_pct"], linea_pct=bt["linea_pct"],
                u_pct=bt["u_pct"], o_pct=bt["o_pct"],
                max_cartillas=bt["max_cartillas"],
                is_active=True, sort_order=i,
                created_at=now_peru().isoformat(),
            ))

def _reload_bingo_types() -> None:
    """Reload the in-memory BINGO_TYPES dict from DB (call after any admin CRUD)."""
    global BINGO_TYPES
    try:
        with db_session() as db:
            rows = db.query(BingoTypeDB).order_by(BingoTypeDB.sort_order, BingoTypeDB.id).all()
            BINGO_TYPES = {r.id: r.to_dict() for r in rows}
    except Exception:
        pass
    if not BINGO_TYPES:
        BINGO_TYPES = dict(_SEED_BINGO_TYPES)

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
def get_upcoming_sessions(limit: int = 50) -> list:
    with db_session() as db:
        rows = (db.query(BingoSession)
                .filter(BingoSession.status.notin_(["cancelled", "finished"]))
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

    def _ball_pool(self):
        balls = BINGO_TYPES.get(self.bingo_type, {}).get("balls", 75)
        return list(range(1, balls + 1))

    def reset(self):
        self.available         = list(range(1, 76))  # overridden after bingo_type is set
        self.drawn: list       = []
        self.last              = None
        self.game_id           = str(uuid.uuid4())[:8].upper()
        self.claimed_winners   = set()
        self.linea_claimed     = set()
        self.u_claimed         = set()
        self.o_claimed         = set()
        self.linea_drawn_at    = None
        self.u_drawn_at        = None
        self.o_drawn_at        = None
        self.winners_log       = []
        self.linea_winners_log = []
        self.u_winners_log     = []
        self.o_winners_log     = []
        self.last_phrase       = None
        self.last_voice        = "es-PE-CamilaNeural"
        self.last_activity     = None
        self.paused            = False
        self.winners_limit     = 99
        self.session_id        = None
        self.bingo_type        = "1sol"
        self.prize_pool        = 0.0
        self.linea_pool        = 0.0
        self.u_pool            = 0.0
        self.o_pool            = 0.0
        self.preparing         = False
        self.prepare_at        = None
        self.prepare_secs      = 60
        self.prepare_sid       = None
        self.session_finished  = False   # True briefly after admin finishes session

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
                "u_claimed":       list(self.u_claimed),
                "o_claimed":       list(self.o_claimed),
                "linea_drawn_at":  self.linea_drawn_at,
                "u_drawn_at":      self.u_drawn_at,
                "o_drawn_at":      self.o_drawn_at,
                "winners_log":          self.winners_log,
                "linea_winners_log":    self.linea_winners_log,
                "u_winners_log":        self.u_winners_log,
                "o_winners_log":        self.o_winners_log,
                "last_phrase":     self.last_phrase,
                "last_voice":      self.last_voice,
                "last_activity":   self.last_activity,
                "paused":          self.paused,
                "winners_limit":   self.winners_limit,
                "session_id":      self.session_id,
                "bingo_type":      self.bingo_type,
                "prize_pool":      self.prize_pool,
                "linea_pool":      self.linea_pool,
                "u_pool":          self.u_pool,
                "o_pool":          self.o_pool,
                "preparing":       self.preparing,
                "prepare_at":      self.prepare_at,
                "prepare_secs":    self.prepare_secs,
                "prepare_sid":     self.prepare_sid,
                "saved_at":        now_peru().isoformat(),
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

            self.available         = data.get("available", list(range(1, 76)))
            self.drawn             = data.get("drawn", [])
            self.last              = data.get("last")
            self.game_id           = data.get("game_id", self.game_id)
            self.claimed_winners   = set(data.get("claimed_winners", []))
            self.linea_claimed     = set(data.get("linea_claimed", []))
            self.u_claimed         = set(data.get("u_claimed", []))
            self.o_claimed         = set(data.get("o_claimed", []))
            self.linea_drawn_at    = data.get("linea_drawn_at", None)
            self.u_drawn_at        = data.get("u_drawn_at", None)
            self.o_drawn_at        = data.get("o_drawn_at", None)
            self.winners_log       = data.get("winners_log", [])
            self.linea_winners_log = data.get("linea_winners_log", [])
            self.u_winners_log     = data.get("u_winners_log", [])
            self.o_winners_log     = data.get("o_winners_log", [])
            self.last_phrase       = data.get("last_phrase")
            self.last_voice        = data.get("last_voice", "es-PE-CamilaNeural")
            self.last_activity     = data.get("last_activity")
            self.paused            = data.get("paused", False)
            self.winners_limit     = data.get("winners_limit", 99)
            self.session_id        = saved_sid
            self.bingo_type        = data.get("bingo_type", "1sol")
            self.prize_pool        = data.get("prize_pool", 0.0)
            self.linea_pool        = data.get("linea_pool", 0.0)
            self.u_pool            = data.get("u_pool", 0.0)
            self.o_pool            = data.get("o_pool", 0.0)
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

# ─── Generador de cartillas — B-I-N-G-O 75 bolas ─────────────────────────────
# Column ranges (1-15, 16-30, 31-45, 46-60, 61-75)
BINGO75_COL_RANGES = [
    list(range(1,  16)),   # B
    list(range(16, 31)),   # I
    list(range(31, 46)),   # N
    list(range(46, 61)),   # G
    list(range(61, 76)),   # O
]
BINGO75_LETTERS = ["B", "I", "N", "G", "O"]

def generate_cartilla_grid_75() -> list:
    """5×5 BINGO card: B(1-15) I(16-30) N(31-45,FREE center) G(46-60) O(61-75)"""
    grid = [[None] * 5 for _ in range(5)]
    for col in range(5):
        pool = BINGO75_COL_RANGES[col]
        if col == 2:                          # N column — 4 numbers + FREE center
            nums = sorted(random.sample(pool, 4))
            for i, row in enumerate([0, 1, 3, 4]):
                grid[row][col] = nums[i]
            # grid[2][2] stays None → FREE
        else:
            nums = sorted(random.sample(pool, 5))
            for row in range(5):
                grid[row][col] = nums[row]
    return grid


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
            created=now_peru().isoformat(),
            generada_por_admin=generada_por_admin,
        )
        db.add(row)
    return {
        "id": cid, "nombre": nombre, "telefono": telefono,
        "voucher_code": voucher_code, "session_id": session_id,
        "bingo_type": bingo_type, "grid": grid,
        "created": now_peru().isoformat(),
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
    return _check_winner_75(grid, drawn)

def _check_winner_75(grid: list, drawn: list) -> dict:
    drawn_set = set(drawn)
    def marked(r, c): return grid[r][c] is None or grid[r][c] in drawn_set  # None = FREE
    nums   = [grid[r][c] for r in range(5) for c in range(5) if grid[r][c] is not None]
    marked_nums = [n for n in nums if n in drawn_set]
    bingo  = all(marked(r, c) for r in range(5) for c in range(5))
    result = {
        "total": 24, "marked": len(marked_nums),
        "bingo": bingo,
        "linea": False, "linea_row": None, "linea_type": None,
        "u_pattern": False, "o_pattern": False,
        "almost": False, "almost_num": None,
    }
    # Formar Letra I: all 5 cells in column I (col 1, numbers 16-30)
    if all(marked(r, 1) for r in range(5)):
        result["linea"] = True; result["linea_row"] = 1; result["linea_type"] = "col"
    # U-pattern: left column (col 0) + right column (col 4) + bottom row (row 4)
    # Forms the letter U — 13 unique cells, none of which is the FREE center
    u_cells = [(r, 0) for r in range(5)] + [(r, 4) for r in range(5)] + [(4, c) for c in range(1, 4)]
    result["u_pattern"] = all(marked(r, c) for r, c in u_cells)
    # O-pattern: full outer border — top row + bottom row + left col + right col
    # 16 unique cells (4 corners shared between rows and cols counted once)
    o_cells = ([(0, c) for c in range(5)] + [(4, c) for c in range(5)] +
               [(r, 0) for r in range(1, 4)] + [(r, 4) for r in range(1, 4)])
    result["o_pattern"] = all(marked(r, c) for r, c in o_cells)
    # Almost bingo (1 away)
    if not bingo and 24 - len(marked_nums) == 1:
        result["almost"]     = True
        result["almost_num"] = next((grid[r][c] for r in range(5) for c in range(5)
                                     if grid[r][c] is not None and grid[r][c] not in drawn_set), None)
    return result

def compute_prize_pool(session_id: str, bingo_type: str) -> dict:
    btype     = BINGO_TYPES.get(bingo_type, BINGO_TYPES["1sol"])
    precio    = btype["precio"]
    prize_pct = btype["prize_pct"]
    linea_pct = btype.get("linea_pct", 0.04)
    u_pct     = btype.get("u_pct", 0.13)
    o_pct     = btype.get("o_pct", 0.15)
    with db_session() as db:
        paid = (db.query(Voucher)
                .filter_by(session_id=session_id, bingo_type=bingo_type)
                .filter(Voucher.payment_status.in_(["approved", "manual_approved"]))
                .count())
    gross  = paid * precio
    linea  = round(gross * linea_pct, 2)
    u_amt  = round(gross * u_pct, 2)
    o_amt  = round(gross * o_pct, 2)
    prize  = round(gross * prize_pct, 2)
    house  = round(gross - prize - linea - u_amt - o_amt, 2)
    return {
        "total_players": paid, "precio_entrada": precio, "gross": gross,
        "prize_pct": prize_pct, "linea_pct": linea_pct, "u_pct": u_pct, "o_pct": o_pct,
        "prize_amount": prize, "linea_amount": linea,
        "u_amount": u_amt, "o_amount": o_amt,
        "house_cut": house,
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
    grid      = cartilla["grid"]
    x0        = 2 * cm
    y0        = ch - 5.7 * cm

    # ── 5×5 B-I-N-G-O card ──────────────────────────────
    BINGO75_COLORS_HEX = ["#3b82f6","#f59e0b","#ef4444","#10b981","#a855f7"]
    col_w = (cw - 4 * cm) / 5
    row_h = 2.0 * cm
    for ci, letter in enumerate(BINGO75_LETTERS):
        r, g, b = hex_to_rgb01(BINGO75_COLORS_HEX[ci])
        cx = x0 + ci * col_w
        c.setFillColorRGB(r * .35, g * .35, b * .35)
        c.roundRect(cx + 1, y0 + 2, col_w - 2, 0.9 * cm, 6, fill=1, stroke=0)
        c.setFillColorRGB(r, g, b)
        c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(cx + col_w / 2, y0 + 0.18 * cm + 2, letter)
    y0 -= 1.0 * cm
    for ri in range(5):
        for ci in range(5):
            num     = grid[ri][ci]
            cx      = x0 + ci * col_w
            cy      = y0 - ri * row_h
            r, g, b = hex_to_rgb01(BINGO75_COLORS_HEX[ci])
            is_free = (ri == 2 and ci == 2)
            if is_free or num in drawn_set:
                c.setFillColorRGB(r * .45, g * .45, b * .45); c.setStrokeColorRGB(r, g, b)
            else:
                c.setFillColorRGB(.07, .14, .20); c.setStrokeColorRGB(.15, .28, .38)
            c.roundRect(cx + 2, cy - row_h + 4, col_w - 4, row_h - 6, 8, fill=1, stroke=1)
            if is_free:
                c.setFillColorRGB(r, g, b)
                c.setFont("Helvetica-Bold", 11)
                c.drawCentredString(cx + col_w / 2, cy - row_h / 2 - 4, "FREE")
            elif num is not None:
                if num in drawn_set:
                    c.setFillColorRGB(r, g, b)
                    c.circle(cx + col_w / 2, cy - row_h / 2 + 2, min(col_w, row_h) * .35, fill=1, stroke=0)
                    c.setFillColorRGB(.04, .07, .10)
                else:
                    c.setFillColorRGB(min(1, r * 1.3), min(1, g * 1.3), min(1, b * 1.3))
                c.setFont("Helvetica-Bold", 20)
                c.drawCentredString(cx + col_w / 2, cy - row_h / 2 - 6, str(num))
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
    c.drawCentredString(cw / 2, 1.4 * cm, f"Números marcados: {marked_count} / 24")
    c.save(); buf.seek(0)
    return buf

def cartilla_to_png(cartilla: dict, drawn: list = None) -> BytesIO:
    drawn_set = set(drawn or [])
    grid  = cartilla["grid"]
    btype = BINGO_TYPES.get(cartilla.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])

    COLS, ROWS = 5, 5
    PAD = 30; HEADER_H = 150; FOOTER_H = 80; CW, CH = 110, 100
    BINGO_RGB = [(59,130,246),(245,158,11),(239,68,68),(16,185,129),(168,85,247)]

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

    y_start = PAD + HEADER_H
    for ci in range(COLS):
        cx    = PAD + ci * CW
        r,g,b = BINGO_RGB[ci]
        draw.rounded_rectangle([cx + 3, y_start - 30, cx + CW - 3, y_start - 4],
                                radius=6, fill=(int(r * .25), int(g * .25), int(b * .25)))
        draw.text((cx + CW // 2, y_start - 17), BINGO75_LETTERS[ci],
                  fill=(r, g, b), anchor="mm", font=_get_font(bold=True, size=28))

    for ri in range(ROWS):
        for ci in range(COLS):
            num   = grid[ri][ci]
            cx    = PAD + ci * CW
            cy    = y_start + ri * CH
            r,g,b = BINGO_RGB[ci]
            is_free = ri == 2 and ci == 2
            if is_free or (num is not None and num in drawn_set):
                cf = (int(r*.45),int(g*.45),int(b*.45)); bf = (r,g,b)
            elif num is None:
                cf, bf = (12,22,32),(20,40,55)
            else:
                cf, bf = (14,32,46),(30,65,90)
            draw.rounded_rectangle([cx+4,cy+4,cx+CW-4,cy+CH-4], radius=10, fill=cf, outline=bf, width=2)
            if is_free:
                draw.text((cx+CW//2,cy+CH//2), "FREE", fill=(r,g,b),
                          anchor="mm", font=_get_font(bold=True, size=18))
            elif num is not None:
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

@app.route("/ayuda")
def ayuda_page():
    return render_template("ayuda.html", cfg=_load_config())

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
    bingo_types = sorted(BINGO_TYPES.values(), key=lambda x: x.get("sort_order", 0))
    return render_template("admin_sessions.html", bingo_types=bingo_types)

@app.route("/admin/payments")
def admin_payments_page():
    return redirect("/admin/caja")

@app.route("/admin/config")
def admin_config_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_config.html")

@app.route("/admin/caja")
def admin_caja_page():
    if not is_admin(): return redirect("/admin/login")
    return render_template("admin_caja.html")

@app.route("/admin/pagos_ganadores")
def admin_pagos_ganadores_page():
    return redirect("/admin/caja")

@app.route("/admin/manual")
def admin_manual_page():
    if not is_admin(): return redirect("/admin/login")
    return send_file(os.path.join(os.path.dirname(__file__), "MANUAL_BINGO_PRO.html"))

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
        if not get_totp_enrolled():
            # Secret set but QR not yet scanned — let them reach setup page
            record_success_login(ip)
            session["totp_setup_pending"] = True
            session["admin_user"] = username
            session["login_ip"]   = ip
            session["login_at"]   = now_peru().isoformat()
            return jsonify({"status": "ok", "setup_2fa": True})
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
    session["login_at"]   = now_peru().isoformat()
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
    if not session_id:
        return jsonify({"error": "no_session",
                        "message": "No hay sesiones disponibles. El administrador debe crear una sesión antes de poder comprar cartillas."}), 400
    # Validar que la sesión existe y coincide con el bingo_type
    if session_id:
        s = get_session(session_id)
        if not s:
            return jsonify({"error": "invalid_session",
                            "message": "La sesión seleccionada no existe."}), 400
        if s.get("bingo_type") != bingo_type:
            return jsonify({"error": "bingo_type_mismatch",
                            "message": f"Esta sesión es de {s.get('bingo_nombre')}. "
                                       f"Selecciona el tipo correcto."}), 400
    is_free = BINGO_TYPES.get(bingo_type, {}).get("precio", 1) == 0
    if not is_free:
        if method != "efectivo" and not ref:
            return jsonify({"error": "missing_ref", "message": "Ingresa el número de operación."}), 400
        if method != "efectivo" and ref and _is_duplicate_payment_ref(ref, method, session_id):
            return jsonify({"error": "duplicate_reference",
                            "message": "Este número de operación ya fue registrado."}), 400
    # Check if same email already has a voucher for this session
    if email and session_id:
        with db_session() as db:
            existing = db.query(Voucher).filter(
                Voucher.email == email,
                Voucher.session_id == session_id,
                Voucher.payment_status.in_(["pending_review", "manual_approved", "approved", "pending"])
            ).first()
            if existing:
                return jsonify({"error": "duplicate_email",
                                "message": "Ya tienes una solicitud registrada para esta sesión."}), 400
    # Check if same phone number already has a voucher for this session
    _celular_norm = re.sub(r'\D', '', celular)  # digits only for comparison
    if _celular_norm and session_id:
        with db_session() as db:
            existing_phone = db.query(Voucher).filter(
                Voucher.session_id == session_id,
                Voucher.payment_status.in_(["pending_review", "manual_approved", "approved", "pending"])
            ).all()
            for v in existing_phone:
                if re.sub(r'\D', '', v.celular or '') == _celular_norm:
                    return jsonify({"error": "duplicate_phone",
                                    "message": "Ya tienes una solicitud registrada con ese número de celular."}), 400

    btype = BINGO_TYPES[bingo_type]
    v_dict = {}
    now = now_peru().isoformat()
    with db_session() as db:
        code = _unique_code(db)
        v = Voucher(
            code=code, nombres=nombres, apellidos=apellidos,
            email=email, celular=celular,
            yape_plin=yape_plin, terms_accepted=terms_accepted,
            bingo_type=bingo_type, bingo_nombre=btype["nombre"],
            precio=btype["precio"], session_id=session_id,
            payment_method=method or "gratis", payment_ref=ref,
            payment_status="pending_review",
            payment_submitted_at=now,
            approved_at=None,
            created=now,
            creado_por="jugador",
        )
        db.add(v)
        db.flush()
        v_dict = v.to_dict()

        # Auto-save player to contacts for future broadcasts (upsert by email/phone)
        phone_e164 = celular if celular and celular.startswith("+") else (f"+{celular}" if celular else "")
        existing = None
        if email:
            existing = db.query(Contact).filter(Contact.email == email).first()
        if not existing and phone_e164:
            existing = db.query(Contact).filter(Contact.phone == phone_e164).first()
        if existing:
            if nombres and not existing.nombre:
                existing.nombre = f"{nombres} {apellidos}".strip()
            if email and not existing.email:
                existing.email = email
            if phone_e164 and not existing.phone:
                existing.phone = phone_e164
        else:
            db.add(Contact(
                nombre=f"{nombres} {apellidos}".strip(),
                email=email or "",
                phone=phone_e164,
                created=now,
            ))

    if email:
        asunto = f"Recibimos tu solicitud - {btype['nombre']} | Bingo Pro"
        ok, err = enviar_email(email, asunto, _email_pago_recibido(v_dict))
        if not ok:
            print(f"[WARN] Email no enviado a {email}: {err}")

    # Notify admin via WhatsApp with one-click approve/reject links
    if ADMIN_WHATSAPP:
        try:
            url_base   = request.host_url.rstrip("/")
            tok_ok     = _quick_token("approve", v_dict["code"])
            tok_no     = _quick_token("reject",  v_dict["code"])
            metodo_str = method.upper() if method else "—"
            ref_str    = ref if ref else "—"
            wa_body = (
                f"💳 *Nuevo pago recibido*\n"
                f"👤 {nombres} {apellidos}\n"
                f"🎱 {btype['nombre']} — S/. {btype['precio']:.2f}\n"
                f"💰 {metodo_str} | Ref: {ref_str}\n"
                f"📱 {celular or '—'}\n"
                f"🎫 Voucher: *{v_dict['code']}*\n\n"
                f"✅ Aprobar:\n{url_base}/admin/quick/approve/{v_dict['code']}?token={tok_ok}\n\n"
                f"❌ Rechazar:\n{url_base}/admin/quick/reject/{v_dict['code']}?token={tok_no}"
            )
            ok_wa, err_wa = enviar_whatsapp(ADMIN_WHATSAPP, wa_body)
            if not ok_wa:
                print(f"[WARN] WhatsApp admin no enviado: {err_wa}")
        except Exception as e:
            print(f"[WARN] WhatsApp admin error: {e}")

    msg = "Solicitud registrada. Recibirás tu código cuando el admin confirme tu registro."
    return jsonify({"status": "ok", "message": msg, "email": email})

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
            payment_status="manual_approved",
            approved_at=now_peru().isoformat(),
            payment_ref=data.get("payment_ref", ""),
            created=now_peru().isoformat(),
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
        v.approved_at    = now_peru().isoformat()
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
                    v2.email_enviado_at     = now_peru().isoformat()
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
        v.rejected_at     = now_peru().isoformat()
        v.rejected_reason = data.get("reason", "")
    return jsonify({"status": "ok"})

@app.route("/admin/quick/approve/<code>")
def quick_approve(code):
    code  = code.strip().upper()
    token = request.args.get("token", "")
    if not _verify_quick_token("approve", code, token):
        return _quick_result("error", "Enlace inválido o expirado.")
    v_dict = {}
    with db_session() as db:
        v = db.query(Voucher).filter_by(code=code).first()
        if not v:
            return _quick_result("error", f"Voucher {code} no encontrado.")
        if v.payment_status in ("manual_approved", "approved"):
            return _quick_result("already", f"El voucher {code} ya estaba aprobado.", code)
        v.payment_status = "manual_approved"
        v.approved_at    = now_peru().isoformat()
        db.flush()
        v_dict = v.to_dict()
    # Send approval email to player
    email = v_dict.get("email", "")
    if email:
        url_base = request.host_url.rstrip("/")
        asunto   = f"🎱 Tu código para {v_dict.get('bingo_nombre','Bingo')} — Bingo Pro"
        ok, err  = enviar_email(email, asunto, _email_codigo_voucher(v_dict, url_base))
        if ok:
            with db_session() as db2:
                v2 = db2.query(Voucher).filter_by(code=code).first()
                if v2:
                    v2.email_codigo_enviado = True
                    v2.email_enviado_at     = now_peru().isoformat()
        else:
            print(f"[WARN] Email aprobación no enviado: {err}")
    return _quick_result("approved", f"Voucher {code} aprobado correctamente.", code)

@app.route("/admin/quick/reject/<code>", methods=["GET", "POST"])
def quick_reject(code):
    code  = code.strip().upper()
    token = request.args.get("token", "")
    if not _verify_quick_token("reject", code, token):
        return _quick_result("error", "Enlace inválido o expirado.")
    if request.method == "POST":
        reason = (request.form.get("reason") or "Pago no verificado").strip()[:200]
        with db_session() as db:
            v = db.query(Voucher).filter_by(code=code).first()
            if not v:
                return _quick_result("error", f"Voucher {code} no encontrado.")
            if v.payment_status == "rejected":
                return _quick_result("already", f"El voucher {code} ya estaba rechazado.", code)
            v.payment_status  = "rejected"
            v.rejected_at     = now_peru().isoformat()
            v.rejected_reason = reason
        return _quick_result("rejected", f"Voucher {code} rechazado.", code)
    # GET — show a small form to enter reason
    return f"""<!DOCTYPE html><html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rechazar pago</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#070d14;color:#ddeeff;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px;box-sizing:border-box}}
  .card{{background:#0d1825;border:1px solid #1a3148;border-radius:16px;padding:28px 24px;max-width:420px;width:100%}}
  h2{{color:#e74c3c;margin:0 0 8px;font-size:1.3rem}}
  p{{color:#4a6b85;font-size:.87rem;margin:0 0 20px}}
  label{{font-size:.83rem;color:#4a6b85;display:block;margin-bottom:6px}}
  textarea{{width:100%;box-sizing:border-box;background:#111f2e;border:1px solid #1a3148;border-radius:8px;
            color:#ddeeff;font-family:inherit;font-size:.9rem;padding:10px;resize:vertical;min-height:80px}}
  button{{margin-top:14px;width:100%;padding:13px;border:none;border-radius:10px;
          background:#e74c3c;color:#fff;font-size:1rem;font-weight:700;cursor:pointer}}
  button:hover{{background:#c0392b}}
</style></head>
<body><div class="card">
  <h2>❌ Rechazar voucher {code}</h2>
  <p>Escribe el motivo del rechazo (opcional). El jugador lo verá en su email.</p>
  <form method="POST">
    <label>Motivo</label>
    <textarea name="reason" placeholder="Ej: El número de operación no coincide..."></textarea>
    <button type="submit">Confirmar rechazo</button>
  </form>
</div></body></html>"""

def _quick_result(status: str, message: str, code: str = "") -> str:
    icons   = {"approved": "✅", "rejected": "❌", "already": "ℹ️", "error": "⚠️"}
    colors  = {"approved": "#00e5b4", "rejected": "#e74c3c", "already": "#f6c343", "error": "#f6c343"}
    icon    = icons.get(status, "ℹ️")
    color   = colors.get(status, "#4a6b85")
    link    = f'<a href="/admin/payments" style="color:{color};font-size:.85rem;">Ver todos los pagos</a>' if code else ""
    return f"""<!DOCTYPE html><html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bingo Pro</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#070d14;color:#ddeeff;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px;box-sizing:border-box}}
  .card{{background:#0d1825;border:1px solid #1a3148;border-radius:16px;padding:32px 24px;
         max-width:400px;width:100%;text-align:center}}
  .icon{{font-size:3rem;margin-bottom:14px}}
  h2{{color:{color};margin:0 0 10px;font-size:1.25rem}}
  p{{color:#4a6b85;font-size:.88rem;margin:0 0 20px;line-height:1.5}}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <h2>{message}</h2>
  {link}
</div></body></html>"""

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
                v.email_reenviado_at   = now_peru().isoformat()
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
        f"<p>Enviado desde: <strong>{EMAIL_FROM}</strong><br>API: Brevo v3</p></body></html>"
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
        v.payment_submitted_at = now_peru().isoformat()
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
        bingo_letter = ["B","I","N","G","O"][min((num-1)//15, 4)]
        letter_phrase = f"{bingo_letter}, {words}"
        if count == 1:
            phrase = f"Primera bolilla, {letter_phrase}"
        elif count == 75:
            phrase = f"Última bolilla, {letter_phrase}. ¡Juego completo!"
        else:
            phrase = f"{letter_phrase}"
        game.last_phrase   = phrase
        game.last_activity = time.time()
        if game.claimed_winners and not game.paused:
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
        bingo_letter = ["B","I","N","G","O"][min((game.last-1)//15, 4)]
        phrase = f"Repito, {bingo_letter}, {num2words(game.last, lang='es')}"
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
        prize_pool  = game.prize_pool
        linea_pool  = game.linea_pool
        u_pool      = game.u_pool
        o_pool      = game.o_pool
        preparing   = game.preparing
        prepare_sid = game.prepare_sid
        sid         = game.session_id

    # During preparing, pools are still 0 — compute estimated prizes from vouchers
    if preparing and (prize_pool == 0):
        est_sid = prepare_sid or sid
        if est_sid:
            try:
                est = compute_prize_pool(est_sid, bt_id)
                prize_pool = est["prize_amount"]
                linea_pool = est["linea_amount"]
                u_pool     = est["u_amount"]
                o_pool     = est["o_amount"]
            except Exception:
                pass

    with game_lock:
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
            "u_winners":          game.u_winners_log,
            "o_winners":          game.o_winners_log,
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
            "bingo_balls":    bt_info.get("balls", 75),
            "bingo_grid":     bt_info.get("grid_type", "75"),
            "prize_pool":     prize_pool,
            "linea_pool":     linea_pool,
            "u_pool":         u_pool,
            "o_pool":         o_pool,
            "preparing":        preparing,
            "prepare_at":       game.prepare_at,
            "prepare_secs":     game.prepare_secs,
            "prepare_sid":      prepare_sid,
            "session_finished": game.session_finished,
            "admin_whatsapp":   _load_config().get("whatsapp", ""),
        })

@app.route("/api/game_stats")
def api_game_stats():
    """Stats públicas para la barra del jugador: jugadores, cartillas, premios, cerca de ganar."""
    with game_lock:
        sid   = game.session_id
        drawn = list(game.drawn)
        prize = game.prize_pool
        linea = game.linea_pool

    cartillas    = load_all_cartillas(session_id=sid) if sid else []
    n_cartillas  = len(cartillas)
    # Jugadores únicos = teléfonos distintos (o nombres si no hay tel)
    n_players    = len({c.get("telefono") or c.get("nombre", "") for c in cartillas})

    # Distribución "cerca de ganar" (cartillas con 1–5 números sin salir para bingo)
    drawn_set = set(drawn)
    close     = {}
    for c in cartillas:
        grid = c.get("grid", [])
        nums = [n for row in grid for n in row if n is not None]
        if not nums:
            continue
        missing = sum(1 for n in nums if n not in drawn_set)
        if 1 <= missing <= 5:
            close[str(missing)] = close.get(str(missing), 0) + 1

    with game_lock:
        u_pool = game.u_pool
        o_pool = game.o_pool

    return jsonify({
        "n_players":    n_players,
        "n_cartillas":  n_cartillas,
        "close_to_win": close,
        "prize_bingo":  prize,
        "prize_linea":  linea,
        "prize_u":      u_pool,
        "prize_o":      o_pool,
    })

@app.route("/api/chat/messages")
def api_chat_messages():
    since = int(request.args.get("since", 0))
    with _chat_lock:
        msgs = [m for m in _chat_msgs if m["id"] > since]
    return jsonify({"messages": msgs})

@app.route("/api/chat/send", methods=["POST"])
@rate_limit(max_calls=8, window_seconds=10)
def api_chat_send():
    global _chat_next_id
    data = request.get_json() or {}
    name = sanitize_text((data.get("name") or "").strip(), 30)
    msg  = sanitize_text((data.get("msg")  or "").strip(), 200)
    if not name:
        return jsonify({"error": "Ingresa tu nombre"}), 400
    if not msg:
        return jsonify({"error": "Mensaje vacío"}), 400
    with _chat_lock:
        entry = {
            "id":    _chat_next_id,
            "name":  name,
            "msg":   msg,
            "ts":    now_peru().strftime("%H:%M"),
            "color": _chat_color(name),
        }
        _chat_msgs.append(entry)
        _chat_next_id += 1
        if len(_chat_msgs) > _CHAT_MAX:
            _chat_msgs.pop(0)
    return jsonify({"ok": True, "id": entry["id"]})

@app.route("/api/chat/clear", methods=["POST"])
def api_chat_clear():
    chk = admin_required()
    if chk: return chk
    global _chat_next_id
    with _chat_lock:
        _chat_msgs.clear()
        _chat_next_id = 1
    return jsonify({"ok": True})

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
    limit = max(1, min(int(data.get("limit", 99)), 99))
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

@app.route("/api/session/<sid>", methods=["GET"])
def api_get_session(sid):
    s = get_session(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    return jsonify({"session": s})

@app.route("/api/cartillas/by_access", methods=["POST"])
@rate_limit(max_calls=10, window_seconds=60)
def api_cartillas_by_access():
    """Devuelve las cartillas de un voucher dado su código único."""
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": "Ingresa tu código de voucher"}), 400
    import json as _json
    with db_session() as db:
        v = db.query(Voucher).filter(
            Voucher.code           == code,
            Voucher.payment_status.in_(["approved", "manual_approved"]),
        ).first()
        if not v:
            return jsonify({"error": "Código no encontrado o pago no aprobado."}), 404
        cartilla_ids = []
        try:
            ids = _json.loads(v.cartillas_ids or "[]")
            for cid in ids:
                cartilla_ids.append({"id": cid, "session_id": v.session_id or "", "bingo_type": v.bingo_type or ""})
        except Exception:
            pass
    return jsonify({"cartillas": cartilla_ids})

@app.route("/api/admin/session", methods=["POST"])
def api_create_session():
    chk = admin_required()
    if chk: return chk

    # ── Validar que haya al menos 1 método de pago configurado ───────────────
    cfg = _load_config()
    metodos = cfg.get("metodos_pago", [])
    has_payment_method = any(
        m.get("activo") and (m.get("numero") or "").strip()
        for m in metodos
        if m.get("id") != "efectivo"   # efectivo no requiere número
    ) or any(
        m.get("activo") and m.get("id") == "efectivo"
        for m in metodos
    )
    nombre_org = (cfg.get("nombre_organizador") or "").strip()
    config_ok  = has_payment_method and nombre_org and nombre_org != "Bingo Pro"
    if not config_ok:
        return jsonify({
            "error": "config_incompleta",
            "message": (
                "Completa la configuración antes de crear una sesión: "
                "necesitas un nombre de organizador (distinto al predeterminado) "
                "y al menos un método de pago activo con número/datos."
            )
        }), 400

    data       = request.get_json() or {}
    bingo_type = data.get("bingo_type", "1sol")
    if bingo_type not in BINGO_TYPES:
        return jsonify({"error": "invalid bingo_type"}), 400
    dt_str = data.get("datetime")
    if not dt_str:
        return jsonify({"error": "datetime required"}), 400
    btype = BINGO_TYPES[bingo_type]

    # ── Validar que no haya otra sesión dentro de 1 hora ──────────────────────
    try:
        new_dt = datetime.fromisoformat(dt_str)
    except ValueError:
        return jsonify({"error": "Formato de fecha inválido"}), 400

    with db_session() as db:
        conflictos = db.query(BingoSession).filter(
            BingoSession.status.notin_(["cancelled", "finished"])
        ).all()
        for c in conflictos:
            try:
                c_dt = datetime.fromisoformat(c.datetime_iso)
            except Exception:
                continue
            diff_min = abs((new_dt - c_dt).total_seconds()) / 60
            if diff_min < 60:
                c_dstr = c_dt.strftime("%d/%m/%Y %H:%M")
                return jsonify({
                    "error": f"Conflicto de horario: ya existe '{c.bingo_nombre}' "
                             f"a las {c_dstr} (diferencia: {int(diff_min)} min). "
                             f"Debe haber al menos 1 hora entre sesiones."
                }), 409
    # ─────────────────────────────────────────────────────────────────────────

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
            created=now_peru().isoformat(),
        )
        db.add(s)
        db.flush()
        s_dict = s.to_dict()
    return jsonify({"status": "ok", "session": s_dict})

@app.route("/api/admin/session/<sid>", methods=["PUT"])
def api_update_session(sid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        s = db.query(BingoSession).filter_by(id=sid).first()
        if not s:
            return jsonify({"error": "not found"}), 404
        if s.status not in ("scheduled", "preparing"):
            return jsonify({"error": "Solo se pueden editar sesiones programadas o en countdown."}), 400

        data       = request.get_json() or {}
        bingo_type = data.get("bingo_type", s.bingo_type)
        dt_str     = data.get("datetime", s.datetime_iso)

        if bingo_type not in BINGO_TYPES:
            return jsonify({"error": "Tipo de bingo inválido"}), 400
        try:
            new_dt = datetime.fromisoformat(dt_str)
        except ValueError:
            return jsonify({"error": "Formato de fecha inválido"}), 400

        # Check time conflict (skip self)
        conflictos = db.query(BingoSession).filter(
            BingoSession.id != sid,
            BingoSession.status.notin_(["cancelled", "finished"])
        ).all()
        for c in conflictos:
            try:
                c_dt = datetime.fromisoformat(c.datetime_iso)
            except Exception:
                continue
            diff_min = abs((new_dt - c_dt).total_seconds()) / 60
            if diff_min < 60:
                c_dstr = c_dt.strftime("%d/%m/%Y %H:%M")
                return jsonify({
                    "error": f"Conflicto de horario con '{c.bingo_nombre}' a las {c_dstr} "
                             f"({int(diff_min)} min de diferencia). Mínimo 1 hora entre sesiones."
                }), 409

        btype = BINGO_TYPES[bingo_type]
        s.bingo_type   = bingo_type
        s.bingo_nombre = btype["nombre"]
        s.bingo_color  = btype["color"]
        s.bingo_precio = btype["precio"]
        s.datetime_iso = dt_str
        s.date         = dt_str[:10]
        s.time         = dt_str[11:16] if len(dt_str) >= 16 else "00:00"
        s.descripcion  = (data.get("descripcion") or "").strip()[:300]
        s.max_players  = int(data.get("max_players") or 0)
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
        s.started_at = now_peru().isoformat()
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
        game.available   = game._ball_pool()   # set correct pool after bingo_type is assigned
        game.prize_pool  = prize["prize_amount"]
        game.linea_pool  = prize["linea_amount"]
        game.u_pool      = prize["u_amount"]
        game.o_pool      = prize["o_amount"]
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
            sx.prepare_at   = now_peru().isoformat()
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
        # Tag each entry with tipo so the payments page can distinguish them
        bingo_wf = [{**w, "tipo": "bingo"} for w in game.winners_log]
        linea_wf = [{**w, "tipo": "linea"} for w in game.linea_winners_log]
        u_wf     = [{**w, "tipo": "u"}     for w in game.u_winners_log]
        o_wf     = [{**w, "tipo": "o"}     for w in game.o_winners_log]
        wf = bingo_wf + linea_wf + u_wf + o_wf
        drawn_snapshot = list(game.drawn)   # capture before reset
        # Clear in-memory logs now that they're persisted
        game.winners_log        = []
        game.linea_winners_log  = []
        game.u_winners_log      = []
        game.o_winners_log      = []
    with db_session() as db:
        sx = db.query(BingoSession).filter_by(id=sid).first()
        if not sx:
            return jsonify({"error": "not found"}), 404
        sx.status       = "finished"
        sx.finished_at  = now_peru().isoformat()
        sx.winners_final= json.dumps(wf, ensure_ascii=False)
        # Write permanent Winner rows (INSERT OR IGNORE via merge-style)
        now_iso = now_peru().isoformat(timespec="seconds")
        for w in wf:
            cid  = w.get("id", "") or w.get("cartilla_id", "")
            tipo = w.get("tipo", "bingo")
            if not cid:
                continue
            if tipo == "linea":
                amt = float(w.get("linea_prize", 0) or 0)
            elif tipo == "u":
                amt = float(w.get("u_prize", 0) or 0)
            elif tipo == "o":
                amt = float(w.get("o_prize", 0) or 0)
            else:
                amt = float(w.get("prize", 0) or 0)
            existing = db.query(Winner).filter_by(session_id=sid, cartilla_id=cid, tipo=tipo).first()
            if existing:
                existing.prize_amount = amt
                existing.paid = bool(w.get("paid", False))
                existing.paid_at = w.get("paid_at", "")
                existing.paid_method = w.get("paid_method", "")
                existing.paid_ref = w.get("paid_ref", "")
                existing.paid_note = w.get("paid_note", "")
            else:
                db.add(Winner(
                    session_id   = sid,
                    cartilla_id  = cid,
                    tipo         = tipo,
                    nombre       = w.get("nombre", ""),
                    apellidos    = w.get("apellidos", ""),
                    email        = w.get("email", ""),
                    celular      = w.get("celular", ""),
                    yape_plin    = w.get("yape_plin", ""),
                    prize_amount = amt,
                    drawn_count  = int(w.get("drawn_count", 0) or 0),
                    claimed_at   = w.get("claimed_at", ""),
                    game_id      = w.get("game_id", ""),
                    n_winners    = int(w.get("n_winners", 1) or 1),
                    split        = bool(w.get("split", False)),
                    merged_o     = float(w.get("merged_o", 0) or 0),
                    merged_u     = float(w.get("merged_u", 0) or 0),
                    linea_row    = w.get("linea_row"),
                    puede_reclamar_bingo = bool(w.get("puede_reclamar_bingo", False)),
                    paid         = bool(w.get("paid", False)),
                    paid_at      = w.get("paid_at", ""),
                    paid_method  = w.get("paid_method", ""),
                    paid_ref     = w.get("paid_ref", ""),
                    paid_note    = w.get("paid_note", ""),
                    created      = w.get("claimed_at") or now_iso,
                ))
    # Limpiar game state si era la sesión activa
    with game_lock:
        if game.session_id == sid:
            game.reset()                  # clears drawn, winners, paused, pools, etc.
            game.session_finished = True  # keep flag so player view can detect the end
            game.save_to_db()
    # Send end-of-bingo summary emails to all players (background)
    if drawn_snapshot:
        url_base = request.host_url.rstrip("/")
        _send_fin_bingo_emails_async(sid, drawn_snapshot, url_base)
    return jsonify({"status": "ok"})

def _cancel_session_cleanup(db, sid: str):
    """Create reimbursements, delete cartillas, send notifications for a cancelled session."""
    sx = db.query(BingoSession).filter_by(id=sid).first()
    if not sx:
        return
    session_nombre = sx.bingo_nombre or sid

    # Find approved vouchers that need reimbursement
    vouchers = db.query(Voucher).filter(
        Voucher.session_id == sid,
        Voucher.payment_status.in_(["manual_approved", "approved"])
    ).all()

    url_base = ""
    try:
        from flask import request as freq
        url_base = freq.host_url.rstrip("/")
    except Exception:
        pass

    for v in vouchers:
        # Skip if reimbursement already exists for this voucher
        existing = db.query(Reimbursement).filter_by(voucher_code=v.code, session_id=sid).first()
        if existing:
            continue
        btype = BINGO_TYPES.get(v.bingo_type, BINGO_TYPES["1sol"])
        amount = btype.get("precio", v.precio or 0.0)
        r = Reimbursement(
            session_id=sid,
            session_nombre=session_nombre,
            voucher_code=v.code,
            nombre=v.nombres or "",
            apellidos=v.apellidos or "",
            email=v.email or "",
            celular=v.celular or "",
            yape_plin=v.yape_plin or "",
            amount=amount,
            status="pending",
            created=now_peru().isoformat(timespec="seconds"),
        )
        db.add(r)
        # Send email notification
        if v.email:
            v_dict = v.to_dict()
            try:
                enviar_email(v.email,
                             f"Sesión cancelada — {session_nombre} | Bingo Pro",
                             _email_sesion_cancelada(v_dict, session_nombre, amount))
            except Exception as e:
                print(f"[WARN] Email reembolso no enviado a {v.email}: {e}")

    # Delete cartillas linked to this session
    db.query(Cartilla).filter_by(session_id=sid).delete()
    # Delete all vouchers linked to this session
    db.query(Voucher).filter_by(session_id=sid).delete()

@app.route("/api/admin/session/<sid>/cancel", methods=["POST"])
def api_cancel_session(sid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        sx = db.query(BingoSession).filter_by(id=sid).first()
        if not sx:
            return jsonify({"error": "not found"}), 404
        _cancel_session_cleanup(db, sid)
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
        _cancel_session_cleanup(db, sid)
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
        rows = (db.query(Winner, BingoSession)
                .join(BingoSession, Winner.session_id == BingoSession.id, isouter=True)
                .order_by(Winner.claimed_at.desc())
                .limit(limit)
                .all())
        result = []
        for w, s in rows:
            result.append({
                "session_id":    w.session_id,
                "bingo_nombre":  s.bingo_nombre if s else "",
                "date":          s.date if s else "",
                "winner_nombre": w.nombre or "",
                "drawn_count":   w.drawn_count or 0,
                "prize":         w.prize_amount if w.tipo == "bingo" else 0,
                "tipo":          w.tipo or "bingo",
            })
    return jsonify({"winners": result})

# ─── Pagos a Ganadores ───────────────────────────────────────────────────────
@app.route("/api/admin/winner_payments")
def api_winner_payments():
    chk = admin_required()
    if chk: return chk
    result = []
    # Read from permanent winners table
    with db_session() as db:
        rows = (db.query(Winner, BingoSession)
                .join(BingoSession, Winner.session_id == BingoSession.id, isouter=True)
                .order_by(BingoSession.datetime_iso.desc())
                .all())
        for w, s in rows:
            tipo = w.tipo or "bingo"
            result.append({
                "session_id":     w.session_id,
                "session_fecha":  s.date       if s else "",
                "session_hora":   s.time       if s else "",
                "bingo_nombre":   s.bingo_nombre if s else "",
                "bingo_type":     s.bingo_type  if s else "",
                "session_status": s.status      if s else "finished",
                "cartilla_id":    w.cartilla_id or "",
                "nombre":         w.nombre      or "",
                "apellidos":      w.apellidos   or "",
                "celular":        w.celular      or "",
                "yape_plin":      w.yape_plin   or "",
                "email":          w.email       or "",
                "prize_amount":   w.prize_amount or 0,
                "prize":          w.prize_amount if tipo == "bingo" else 0,
                "linea_prize":    w.prize_amount if tipo == "linea" else 0,
                "u_prize":        w.prize_amount if tipo == "u"     else 0,
                "o_prize":        w.prize_amount if tipo == "o"     else 0,
                "claimed_at":     w.claimed_at  or "",
                "drawn_count":    w.drawn_count or 0,
                "tipo":           tipo,
                "paid":           bool(w.paid),
                "paid_at":        w.paid_at     or "",
                "paid_method":    w.paid_method or "",
                "paid_ref":       w.paid_ref    or "",
                "paid_note":      w.paid_note   or "",
            })
    # Also include current in-memory game winners not yet finished
    with game_lock:
        active_sid = game.session_id
        live_all   = (
            [({**w, "tipo": "bingo"}) for w in game.winners_log] +
            [({**w, "tipo": "linea"}) for w in game.linea_winners_log] +
            [({**w, "tipo": "u"})     for w in game.u_winners_log] +
            [({**w, "tipo": "o"})     for w in game.o_winners_log]
        ) if active_sid else []
    db_keys = {(r["session_id"], r["cartilla_id"], r["tipo"]) for r in result}
    for w in live_all:
        key = (active_sid or "", w.get("id", ""), w.get("tipo", "bingo"))
        if key not in db_keys:
            tipo = w.get("tipo", "bingo")
            result.append({
                "session_id":     active_sid or "—",
                "session_fecha":  "",
                "session_hora":   "",
                "bingo_nombre":   w.get("bingo_nombre", "Juego activo"),
                "bingo_type":     w.get("bingo_type", ""),
                "session_status": "active",
                "cartilla_id":    w.get("id", ""),
                "nombre":         w.get("nombre", ""),
                "apellidos":      w.get("apellidos", ""),
                "celular":        w.get("celular", ""),
                "yape_plin":      w.get("yape_plin", ""),
                "email":          w.get("email", ""),
                "prize":          w.get("prize", 0)       if tipo == "bingo" else 0,
                "linea_prize":    w.get("linea_prize", 0) if tipo == "linea" else 0,
                "u_prize":        w.get("u_prize", 0)     if tipo == "u"     else 0,
                "o_prize":        w.get("o_prize", 0)     if tipo == "o"     else 0,
                "claimed_at":     w.get("claimed_at", ""),
                "drawn_count":    w.get("drawn_count", 0),
                "tipo":           tipo,
                "paid":           False,
                "paid_at":        "",
                "paid_method":    "",
                "paid_ref":       "",
                "paid_note":      "",
            })
    return jsonify({"payments": result})


@app.route("/api/admin/winner_payments/pay", methods=["POST"])
def api_pay_winner():
    chk = admin_required()
    if chk: return chk
    data        = request.get_json() or {}
    session_id  = (data.get("session_id")  or "").strip()
    cartilla_id = (data.get("cartilla_id") or "").strip().upper()
    paid_ref    = (data.get("paid_ref")    or "").strip()[:100]
    paid_method = (data.get("paid_method") or "efectivo").strip()
    paid_note   = (data.get("paid_note")   or "").strip()[:300]

    if not session_id or not cartilla_id:
        return jsonify({"error": "session_id y cartilla_id requeridos"}), 400

    paid_at_iso = now_peru().isoformat()
    updated = False

    # Update permanent winners table (all tipos for this cartilla in that session)
    with db_session() as db:
        winner_rows = db.query(Winner).filter_by(session_id=session_id, cartilla_id=cartilla_id).all()
        for wr in winner_rows:
            wr.paid        = True
            wr.paid_at     = paid_at_iso
            wr.paid_method = paid_method
            wr.paid_ref    = paid_ref
            wr.paid_note   = paid_note
            updated = True
        # Also keep sessions.winners_final JSON in sync
        if updated:
            sx = db.query(BingoSession).filter_by(id=session_id).first()
            if sx:
                try:
                    wf = json.loads(sx.winners_final or "[]")
                    for w in wf:
                        if w.get("id", "") == cartilla_id or w.get("cartilla_id", "") == cartilla_id:
                            w["paid"] = True; w["paid_at"] = paid_at_iso
                            w["paid_method"] = paid_method; w["paid_ref"] = paid_ref
                            w["paid_note"] = paid_note
                    sx.winners_final = json.dumps(wf, ensure_ascii=False)
                except Exception:
                    pass

    # Also update in-memory game winners (active game, not yet finished)
    if not updated:
        with game_lock:
            all_logs = game.winners_log + game.linea_winners_log + game.u_winners_log + game.o_winners_log
            for w in all_logs:
                if w.get("id") == cartilla_id and game.session_id == session_id:
                    w["paid"]        = True
                    w["paid_at"]     = paid_at_iso
                    w["paid_method"] = paid_method
                    w["paid_ref"]    = paid_ref
                    w["paid_note"]   = paid_note
                    updated = True
            if updated:
                game.save_to_db()

    if not updated:
        return jsonify({"error": "Ganador no encontrado"}), 404
    return jsonify({"status": "ok", "message": "Pago registrado correctamente"})


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
        # Build winners map: session_id → list of Winner rows
        all_winners = db.query(Winner).all()
        winners_by_sid = {}
        for wr in all_winners:
            winners_by_sid.setdefault(wr.session_id, []).append(wr)
        # Build paid reimbursements map: session_id → total paid amount
        all_reimbs = db.query(Reimbursement).all()
        reimbs_paid_by_sid: dict[str, float] = {}
        for r in all_reimbs:
            if r.status == "paid":
                reimbs_paid_by_sid[r.session_id] = \
                    reimbs_paid_by_sid.get(r.session_id, 0.0) + (r.amount or 0.0)
        total_reimbs_paid = sum(reimbs_paid_by_sid.values())
        for s in sessions:
            sw = winners_by_sid.get(s.id, [])
            # Only count winners whose prize has been paid out
            premios   = sum(wr.prize_amount or 0 for wr in sw if wr.paid)
            total_prem += premios
            reimbs    = reimbs_paid_by_sid.get(s.id, 0.0)
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
                "ganancia": round(recaudado - premios - reimbs, 2),
                "ganadores": [wr.to_dict() for wr in sw],
            })
        total_vouchers = len(vouchers)

    return jsonify({
        "resumen": {
            "total_recaudado":  round(total_rec, 2),
            "total_premios":    round(total_prem, 2),
            "total_reembolsos": round(total_reimbs_paid, 2),
            "ganancia_bruta":   round(total_rec - total_prem - total_reimbs_paid, 2),
            "total_sesiones":   len(sesiones),
            "total_vouchers":   total_vouchers,
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
        grid = generate_cartilla_grid_75()
        c    = save_cartilla(nombre, grid, voucher_code=code,
                             session_id=session_id, bingo_type=bingo_type,
                             generada_por_admin=por_admin)
        results.append(c)
        if code:
            mark_voucher_cartilla(code, c["id"])

    # Send email with cartilla PNG(s) attached whenever there's a voucher with an email
    if vinfo and vinfo.get("email"):
        _send_cartilla_email_async(vinfo, results, request.host_url.rstrip("/"))

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
    vinfo      = get_voucher_info(code) if code else None
    session_id = (vinfo.get("session_id") if vinfo else None) or ""
    bingo_type = (vinfo.get("bingo_type") if vinfo else "1sol")
    # If no session from voucher, use current game session or most recent pending/active session
    if not session_id:
        with game_lock:
            session_id = game.session_id or ""
    if not session_id:
        with db_session() as db:
            sx = db.query(BingoSession).filter(
                BingoSession.status.in_(["active", "preparing", "pending"])
            ).order_by(BingoSession.created.desc()).first()
            if sx:
                session_id = sx.id
    if not grid or len(grid) != 5 or any(len(r) != 5 for r in grid):
        return jsonify({"error": "grid invalido"}), 400
    nums = [n for row in grid for n in row if n is not None]
    if len(nums) != 24:
        return jsonify({"error": "Se requieren 24 numeros"}), 400
    c = save_cartilla(nombre, grid, voucher_code=code,
                      session_id=session_id, bingo_type=bingo_type,
                      generada_por_admin=is_admin())
    if code:
        mark_voucher_cartilla(code, c["id"])

    # Send email with cartilla PNG attached whenever there's a voucher with an email
    if vinfo and vinfo.get("email"):
        _send_cartilla_email_async(vinfo, [c], request.host_url.rstrip("/"))

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
    # Validate grid is 5×5 — old 3×9 cartillas would crash check_winner
    grid = c.get("grid") or []
    if len(grid) != 5 or any(len(row) != 5 for row in grid):
        return jsonify({"error": "invalid grid format"}), 404
    # Single DB query: check session exists, not cancelled, and get its status
    csid = c.get("session_id", "")
    session_status = None
    session_nombre = None
    if csid:
        with db_session() as db:
            sx = db.query(BingoSession).filter_by(id=csid).first()
            if not sx or sx.status == "cancelled":
                return jsonify({"error": "session cancelled"}), 404
            session_status = sx.status
            session_nombre = sx.bingo_nombre
    with game_lock:
        drawn2 = list(game.drawn)
    result = check_winner(grid, drawn2)
    result.update({
        "id": c["id"], "nombre": c["nombre"], "grid": grid,
        "bingo_type": c.get("bingo_type", "1sol"),
        "session_id": c.get("session_id", ""),
        "session_status": session_status,
        "session_nombre": session_nombre,
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
        if game.session_id:
            c_sid = c.get("session_id", "")
            if not c.get("generada_por_admin") and c_sid != game.session_id:
                return jsonify({"error": "session_mismatch",
                                "message": "Esta cartilla no pertenece a la sesión activa."}), 403

        if not chk.get("bingo"):
            return jsonify({"ok": False, "error": "not_bingo",
                            "marked": chk.get("marked"), "total": chk.get("total")}), 400

        if cid in claimed:
            entry = next((w for w in game.winners_log if w["id"] == cid), None)
            return jsonify({"ok": True, "already": True, "game_id": gid, "winner": entry})

        claimed.add(cid)
        btype     = BINGO_TYPES.get(game.bingo_type, BINGO_TYPES["1sol"])
        n_winners = len(claimed)

        # On the first BINGO claim: merge any unclaimed O/U pools into BINGO prize
        merged_o = 0.0
        merged_u = 0.0
        if n_winners == 1:
            if not game.o_claimed and game.o_pool > 0:
                merged_o = game.o_pool
                game.prize_pool += game.o_pool
                game.o_pool = 0.0
            if not game.u_claimed and game.u_pool > 0:
                merged_u = game.u_pool
                game.prize_pool += game.u_pool
                game.u_pool = 0.0

        prize_each = round(game.prize_pool / n_winners, 2)

        # Capture previous winners that need corrected emails (tie scenario)
        prev_winners_to_notify = []
        for prev in game.winners_log:
            if prev.get("game_id") == gid:
                prev["prize"]     = prize_each
                prev["n_winners"] = n_winners
                prev["split"]     = n_winners > 1
                prev_winners_to_notify.append(dict(prev))  # snapshot after update

        winner = {
            "id":           cid,
            "nombre":       c.get("nombre"),
            "apellidos":    apellidos,
            "yape_plin":    yape_plin,
            "email":        winner_email,
            "celular":      celular,
            "claimed_at":   now_peru().isoformat(),
            "drawn_count":  len(drawn2),
            "game_id":      gid,
            "prize":        prize_each,
            "n_winners":    n_winners,
            "split":        n_winners > 1,
            "bingo_type":   btype["id"],
            "bingo_nombre": btype["nombre"],
            "merged_o":     merged_o,
            "merged_u":     merged_u,
        }
        game.winners_log.append(winner)
        game.paused = True  # always pause on bingo/apagón
        game.save_to_db()

    # If this is a tie (n_winners > 1), re-send corrected emails to previous winners
    # (they already received an email with the full/wrong prize amount)
    if n_winners > 1:
        for prev_w in prev_winners_to_notify:
            prev_email = prev_w.get("email", "")
            if prev_email:
                prev_asunto = f"Actualización: Premio compartido — Ganaste S/. {prize_each:.2f} — {btype['nombre']}"
                ok, err = enviar_email(prev_email, prev_asunto, _email_ganador(prev_w, btype))
                if not ok:
                    print(f"[WARN] Email corrección ganador no enviado a {prev_email}: {err}")

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
        if game.session_id:
            c_sid = c.get("session_id", "")
            if not c.get("generada_por_admin") and c_sid != game.session_id:
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
            "claimed_at":   now_peru().isoformat(),
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


@app.route("/api/winner/claim_u", methods=["POST"])
@rate_limit(max_calls=5, window_seconds=10)
def api_winner_claim_u():
    data = request.get_json() or {}
    cid  = (data.get("cid") or "").strip().upper()
    if not cid: return jsonify({"error": "missing_cid"}), 400
    c = load_cartilla(cid)
    if not c: return jsonify({"error": "not_found"}), 404

    yape_plin = ""; winner_email = ""; celular = ""; apellidos = ""
    with db_session() as db:
        v_code = c.get("voucher_code", "")
        if v_code:
            v = db.query(Voucher).filter_by(code=v_code).first()
            if v:
                yape_plin = v.yape_plin or ""; winner_email = v.email or ""
                celular = v.celular or ""; apellidos = v.apellidos or ""

    with game_lock:
        drawn2 = list(game.drawn)
        gid    = game.game_id
        u_cl   = game.u_claimed
        chk    = check_winner(c["grid"], drawn2)

        if game.session_id:
            c_sid = c.get("session_id", "")
            if not c.get("generada_por_admin") and c_sid != game.session_id:
                return jsonify({"error": "session_mismatch"}), 403

        if not chk.get("u_pattern"):
            return jsonify({"ok": False, "error": "not_u_pattern"}), 400
        if cid in u_cl:
            entry = next((w for w in game.u_winners_log if w["id"] == cid), None)
            return jsonify({"ok": True, "already": True, "winner": entry})

        current_drawn = len(drawn2)
        if game.u_drawn_at is not None and current_drawn > game.u_drawn_at:
            return jsonify({"ok": False, "error": "u_closed",
                            "message": "La U ya fue ganada en una bolilla anterior."}), 400
        if game.u_drawn_at is None:
            game.u_drawn_at = current_drawn

        u_cl.add(cid)
        n_u    = len(u_cl)
        u_each = round(game.u_pool / n_u, 2)

        for prev in game.u_winners_log:
            if prev.get("game_id") == gid:
                prev["u_prize"] = u_each; prev["n_winners"] = n_u; prev["split"] = n_u > 1

        u_winner = {
            "id": cid, "nombre": c.get("nombre"), "apellidos": apellidos,
            "yape_plin": yape_plin, "email": winner_email, "celular": celular,
            "claimed_at": now_peru().isoformat(), "drawn_count": len(drawn2),
            "game_id": gid, "u_prize": u_each, "n_winners": n_u, "split": n_u > 1,
            "puede_reclamar_bingo": True,
        }
        game.u_winners_log.append(u_winner)
        btype = BINGO_TYPES.get(game.bingo_type, BINGO_TYPES["1sol"])
        game.save_to_db()

    # Send winner email (outside lock)
    if winner_email:
        asunto = f"Felicidades! Ganaste la U — S/. {u_each:.2f} — {btype['nombre']}"
        ok, err = enviar_email(winner_email, asunto, _email_ganador(u_winner, btype, pattern="u"))
        if not ok:
            print(f"[WARN] Email U-winner no enviado a {winner_email}: {err}")

    return jsonify({
        "ok": True, "already": False, "game_id": gid, "winner": u_winner,
        "u_prize": u_each, "n_winners": n_u, "split": n_u > 1,
        "nombre": c.get("nombre"), "puede_reclamar_bingo": True,
    })


@app.route("/api/winner/claim_o", methods=["POST"])
@rate_limit(max_calls=5, window_seconds=10)
def api_winner_claim_o():
    data = request.get_json() or {}
    cid  = (data.get("cid") or "").strip().upper()
    if not cid: return jsonify({"error": "missing_cid"}), 400
    c = load_cartilla(cid)
    if not c: return jsonify({"error": "not_found"}), 404

    yape_plin = ""; winner_email = ""; celular = ""; apellidos = ""
    with db_session() as db:
        v_code = c.get("voucher_code", "")
        if v_code:
            v = db.query(Voucher).filter_by(code=v_code).first()
            if v:
                yape_plin = v.yape_plin or ""; winner_email = v.email or ""
                celular = v.celular or ""; apellidos = v.apellidos or ""

    with game_lock:
        drawn2 = list(game.drawn)
        gid    = game.game_id
        o_cl   = game.o_claimed
        chk    = check_winner(c["grid"], drawn2)

        if game.session_id:
            c_sid = c.get("session_id", "")
            if not c.get("generada_por_admin") and c_sid != game.session_id:
                return jsonify({"error": "session_mismatch"}), 403

        if not chk.get("o_pattern"):
            return jsonify({"ok": False, "error": "not_o_pattern"}), 400
        if cid in o_cl:
            entry = next((w for w in game.o_winners_log if w["id"] == cid), None)
            return jsonify({"ok": True, "already": True, "winner": entry})

        current_drawn = len(drawn2)
        if game.o_drawn_at is not None and current_drawn > game.o_drawn_at:
            return jsonify({"ok": False, "error": "o_closed",
                            "message": "La O ya fue ganada en una bolilla anterior."}), 400
        if game.o_drawn_at is None:
            game.o_drawn_at = current_drawn

        o_cl.add(cid)
        n_o    = len(o_cl)
        o_each = round(game.o_pool / n_o, 2)

        for prev in game.o_winners_log:
            if prev.get("game_id") == gid:
                prev["o_prize"] = o_each; prev["n_winners"] = n_o; prev["split"] = n_o > 1

        o_winner = {
            "id": cid, "nombre": c.get("nombre"), "apellidos": apellidos,
            "yape_plin": yape_plin, "email": winner_email, "celular": celular,
            "claimed_at": now_peru().isoformat(), "drawn_count": len(drawn2),
            "game_id": gid, "o_prize": o_each, "n_winners": n_o, "split": n_o > 1,
            "puede_reclamar_bingo": True,
        }
        game.o_winners_log.append(o_winner)
        btype = BINGO_TYPES.get(game.bingo_type, BINGO_TYPES["1sol"])
        game.save_to_db()

    # Send winner email (outside lock)
    if winner_email:
        asunto = f"Felicidades! Ganaste la O — S/. {o_each:.2f} — {btype['nombre']}"
        ok, err = enviar_email(winner_email, asunto, _email_ganador(o_winner, btype, pattern="o"))
        if not ok:
            print(f"[WARN] Email O-winner no enviado a {winner_email}: {err}")

    return jsonify({
        "ok": True, "already": False, "game_id": gid, "winner": o_winner,
        "o_prize": o_each, "n_winners": n_o, "split": n_o > 1,
        "nombre": c.get("nombre"), "puede_reclamar_bingo": True,
    })


# ─── Email broadcast ──────────────────────────────────────────────────────────

def _email_broadcast(session_dict: dict, btype: dict, url_base: str, extra_info: str = "") -> str:
    nombre_bingo = btype.get("nombre", "Bingo Pro")
    emoji        = btype.get("emoji", "🎯")
    precio       = btype.get("precio", 0.0)
    color        = btype.get("color", "#00e5b4")
    dt_raw       = session_dict.get("datetime_iso", "")
    dt_str       = dt_raw.replace("T", " ")[:16] if dt_raw else "Por confirmar"
    descripcion  = session_dict.get("descripcion", "")
    sid          = session_dict.get("id", "")
    url_cartillas = f"{url_base}/cartillas"

    prize_pct  = btype.get("prize_pct", 0.55)
    linea_pct  = btype.get("linea_pct", 0.04)
    u_pct      = btype.get("u_pct", 0.13)
    o_pct      = btype.get("o_pct", 0.15)

    desc_block = (f"<div style='background:#111f2e;border-radius:8px;padding:10px 14px;"
                  f"margin:12px 0;font-size:.85rem;color:#ddeeff'>"
                  f"📝 {descripcion}</div>") if descripcion else ""

    # Eye-catching extra banner
    import html as _html
    extra_block = ""
    if extra_info:
        safe_extra = _html.escape(extra_info).replace("\n", "<br>")
        extra_block = f"""
  <div style="margin-bottom:20px;border-radius:14px;overflow:hidden">
    <div style="background:linear-gradient(135deg,{color}22 0%,{color}44 50%,{color}22 100%);
                border:2px solid {color};border-radius:14px;padding:20px 24px;text-align:center;
                position:relative">
      <div style="font-size:2rem;margin-bottom:8px">🔥</div>
      <div style="font-size:1.05rem;font-weight:900;color:{color};line-height:1.5;
                  letter-spacing:.5px;text-shadow:0 0 20px {color}66">
        {safe_extra}
      </div>
    </div>
  </div>"""

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.6}}}}</style>
</head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 20px">

  <div style="text-align:center;margin-bottom:24px">
    <div style="font-size:3rem;margin-bottom:8px">{emoji}</div>
    <h1 style="font-family:Arial,sans-serif;font-size:2rem;color:{color};letter-spacing:3px;margin:0">
      ¡NUEVO BINGO!
    </h1>
    <p style="color:#4a6b85;margin:6px 0 0;font-size:.9rem">{nombre_bingo}</p>
  </div>

  {extra_block}

  <div style="background:#0d1825;border:2px solid {color};border-radius:16px;padding:24px;margin-bottom:20px">

    <div style="display:flex;justify-content:space-between;align-items:center;
                background:#111f2e;border-radius:10px;padding:14px;margin-bottom:16px">
      <div>
        <div style="font-size:.68rem;color:#4a6b85;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px">📅 Fecha y hora</div>
        <div style="font-size:1.2rem;font-weight:900;color:#ddeeff">{dt_str}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:.68rem;color:#4a6b85;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px">🎫 Precio</div>
        <div style="font-size:1.4rem;font-weight:900;color:{color}">S/. {precio:.2f}</div>
      </div>
    </div>

    {desc_block}

    <div style="margin-bottom:16px">
      <div style="font-size:.72rem;color:#4a6b85;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px">💰 Distribución de premios</div>
      <table style="width:100%;border-collapse:collapse;font-size:.85rem">
        <tr style="background:rgba(0,229,180,.06)">
          <td style="padding:7px 10px">🎉 Cartón lleno o Bingo</td>
          <td style="padding:7px 10px;text-align:right;color:#00e5b4;font-weight:900">{prize_pct*100:.0f}% mínimo</td>
        </tr>
        <tr>
          <td style="padding:7px 10px">⭕ Formar Letra O</td>
          <td style="padding:7px 10px;text-align:right;color:#ec4899">{o_pct*100:.0f}%</td>
        </tr>
        <tr style="background:rgba(0,229,180,.03)">
          <td style="padding:7px 10px">🔷 Formar Letra U</td>
          <td style="padding:7px 10px;text-align:right;color:#3b82f6">{u_pct*100:.0f}%</td>
        </tr>
        <tr>
          <td style="padding:7px 10px">⭐ LÍNEA</td>
          <td style="padding:7px 10px;text-align:right;color:#f6c343">{linea_pct*100:.0f}%</td>
        </tr>
      </table>
      <div style="font-size:.73rem;color:#3a5568;margin-top:6px;padding:0 4px">
        * Si nadie gana O o U, esos premios se acumulan al BINGO. El 100% vuelve a los jugadores.
      </div>
    </div>

    <div style="text-align:center">
      <a href="{url_cartillas}" style="display:inline-block;background:{color};color:#041015;
         padding:14px 36px;border-radius:12px;font-weight:900;font-size:1.1rem;
         text-decoration:none;letter-spacing:1px">
        🎴 COMPRAR CARTILLA
      </a>
      <div style="font-size:.72rem;color:#4a6b85;margin-top:10px">
        Asegura tu lugar antes de que se agoten los cupos
      </div>
    </div>

  </div>

  <p style="text-align:center;color:#1a3148;font-size:.72rem;margin:0">
    Bingo Pro Web v9.0 · {EMAIL_NOMBRE}<br>
    <span style="color:#0f2030">Recibiste este email porque participaste en un Bingo anterior.</span><br>
    <span style="color:#0f2030">Por favor no respondas a este correo — esta dirección no está monitoreada.</span>
  </p>

</div></body></html>"""


@app.route("/api/admin/emails")
def api_admin_emails():
    """Lista de todos los emails únicos (jugadores + contactos manuales)."""
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        v_rows = db.query(
            Voucher.email, Voucher.nombres, Voucher.apellidos,
            Voucher.payment_status, Voucher.created,
        ).filter(Voucher.email != "").order_by(Voucher.created.desc()).all()
        v_rows = [(r.email, r.nombres, r.apellidos, r.payment_status, r.created) for r in v_rows]
        c_rows = db.query(Contact.email, Contact.nombre, Contact.created).filter(Contact.email != "").all()
        c_rows = [(r.email, r.nombre, r.created) for r in c_rows]

    seen = {}
    for email, nombres, apellidos, payment_status, created in v_rows:
        e = (email or "").strip().lower()
        if not e: continue
        if e not in seen:
            seen[e] = {
                "email":   e,
                "nombre":  f"{nombres or ''} {apellidos or ''}".strip(),
                "status":  payment_status,
                "created": created,
                "total":   1,
                "source":  "player",
            }
        else:
            seen[e]["total"] += 1
    for email, nombre, created in c_rows:
        e = (email or "").strip().lower()
        if not e: continue
        if e not in seen:
            seen[e] = {
                "email":   e,
                "nombre":  nombre or "",
                "status":  "contact",
                "created": created,
                "total":   1,
                "source":  "contact",
            }

    records = sorted(seen.values(), key=lambda x: x["created"] or "", reverse=True)
    return jsonify({"emails": records, "total": len(records)})


@app.route("/api/admin/session/<sid>/broadcast", methods=["POST"])
def api_broadcast_session(sid):
    """Envía email masivo a todos los emails registrados anunciando la sesión."""
    chk = admin_required()
    if chk: return chk
    s = get_session(sid)
    if not s:
        return jsonify({"error": "not found"}), 404

    data_req = request.get_json() or {}
    extra_info = (data_req.get("extra_info") or "").strip()
    btype    = BINGO_TYPES.get(s.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    url_base = request.host_url.rstrip("/")
    asunto   = f"🎱 ¡Nuevo Bingo! {btype['nombre']} — {s.get('datetime_iso','')[:10]} | Bingo Pro"
    html     = _email_broadcast(s, btype, url_base, extra_info=extra_info)

    # Collect all unique emails (players + manual contacts)
    with db_session() as db:
        v_emails = [r.email for r in db.query(Voucher.email).filter(Voucher.email != "").distinct().all()]
        c_emails = [r.email for r in db.query(Contact.email).filter(Contact.email != "").all()]
    emails = list({(e or "").strip().lower() for e in v_emails + c_emails if e and e.strip()})

    sent = 0; failed = 0; errors = []
    for email in emails:
        ok, err = enviar_email(email, asunto, html)
        if ok:
            sent += 1
        else:
            failed += 1
            if len(errors) < 5:
                errors.append(f"{email}: {err}")

    return jsonify({"sent": sent, "failed": failed, "total": len(emails), "errors": errors})


@app.route("/admin/emails")
def admin_emails_page():
    return redirect("/admin/jugadores")

@app.route("/admin/contacts")
def admin_contacts_page():
    return redirect("/admin/jugadores")

@app.route("/admin/jugadores")
def admin_jugadores_page():
    chk = admin_required()
    if chk: return chk
    return render_template("admin_jugadores.html")

@app.route("/api/admin/reimbursements", methods=["GET"])
def api_reimbursements():
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        rows = db.query(Reimbursement).order_by(Reimbursement.created.desc()).all()
        return jsonify({"reimbursements": [r.to_dict() for r in rows]})

@app.route("/api/admin/reimbursements/<int:rid>/pay", methods=["POST"])
def api_pay_reimbursement(rid):
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}
    with db_session() as db:
        r = db.query(Reimbursement).filter_by(id=rid).first()
        if not r:
            return jsonify({"error": "not found"}), 404
        r.status   = "paid"
        r.paid_at  = now_peru().isoformat(timespec="seconds")
        r.paid_ref  = (data.get("ref") or "").strip()[:80]
        r.paid_note = (data.get("note") or "").strip()[:200]
    return jsonify({"status": "ok"})

@app.route("/api/admin/reimbursements/<int:rid>/delete", methods=["DELETE"])
def api_delete_reimbursement(rid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        r = db.query(Reimbursement).filter_by(id=rid).first()
        if not r:
            return jsonify({"error": "not found"}), 404
        db.delete(r)
    return jsonify({"status": "ok"})

@app.route("/api/admin/contacts", methods=["GET"])
def api_contacts_list():
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        contacts = db.query(Contact).order_by(Contact.id.desc()).all()
        return jsonify({"contacts": [c.to_dict() for c in contacts], "total": len(contacts)})

@app.route("/api/admin/contacts", methods=["POST"])
def api_contact_create():
    chk = admin_required()
    if chk: return chk
    data    = request.get_json() or {}
    nombre  = (data.get("nombre") or "").strip()
    email   = (data.get("email")  or "").strip().lower()
    phone   = (data.get("phone")  or "").strip()
    if not email and not phone:
        return jsonify({"error": "Ingresa al menos un email o teléfono"}), 400
    if phone:
        phone = _normalize_phone(phone, TWILIO_COUNTRY)
    with db_session() as db:
        if email:
            dup = db.query(Contact).filter(Contact.email == email).first()
            if dup:
                return jsonify({"error": "Ya existe un contacto con ese email"}), 400
        if phone:
            dup = db.query(Contact).filter(Contact.phone == phone).first()
            if dup:
                return jsonify({"error": "Ya existe un contacto con ese teléfono"}), 400
        c = Contact(
            nombre=nombre, email=email, phone=phone,
            created=now_peru().isoformat(timespec="seconds"),
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return jsonify({"contact": c.to_dict()})

@app.route("/api/subscribe", methods=["POST"])
def api_public_subscribe():
    """Public endpoint — lets any visitor subscribe to WhatsApp notifications."""
    data   = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    phone  = (data.get("phone")  or "").strip()
    if not phone:
        return jsonify({"error": "Ingresa tu número de WhatsApp"}), 400
    phone = _normalize_phone(phone, TWILIO_COUNTRY)
    if not phone:
        return jsonify({"error": "Número de teléfono inválido"}), 400
    with db_session() as db:
        dup = db.query(Contact).filter(Contact.phone == phone).first()
        if dup:
            return jsonify({"ok": True, "already": True})
        c = Contact(
            nombre=nombre, email="", phone=phone,
            created=now_peru().isoformat(timespec="seconds"),
        )
        db.add(c)
        db.commit()
    return jsonify({"ok": True, "already": False})

@app.route("/api/admin/contacts/<int:cid>", methods=["DELETE"])
def api_contact_delete(cid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        c = db.query(Contact).filter_by(id=cid).first()
        if not c:
            return jsonify({"error": "not found"}), 404
        db.delete(c)
        db.commit()
    return jsonify({"status": "ok"})

@app.route("/api/admin/contacts/dedup", methods=["POST"])
def api_contacts_dedup():
    """Remove contacts whose email or phone already exists in the Voucher table."""
    chk = admin_required()
    if chk: return chk
    removed = 0
    with db_session() as db:
        voucher_emails = {(v.email or "").strip().lower()
                         for v in db.query(Voucher.email).all() if v.email}
        voucher_phones = {(v.celular or "").strip()
                         for v in db.query(Voucher.celular).all() if v.celular}
        contacts = db.query(Contact).all()
        for c in contacts:
            c_email = (c.email or "").strip().lower()
            c_phone = (c.phone or "").strip()
            if (c_email and c_email in voucher_emails) or \
               (c_phone and c_phone in voucher_phones):
                db.delete(c)
                removed += 1
    return jsonify({"removed": removed})

@app.route("/api/admin/contacts/import", methods=["POST"])
def api_contacts_import():
    """Bulk import contacts from CSV text. Columns: nombre, email, phone (header optional)."""
    chk = admin_required()
    if chk: return chk
    data    = request.get_json() or {}
    csv_raw = (data.get("csv") or "").strip()
    if not csv_raw:
        return jsonify({"error": "No data"}), 400

    import csv, io
    reader  = csv.reader(io.StringIO(csv_raw))
    rows    = list(reader)
    # Auto-detect header
    if rows and any(h.lower() in ("nombre","email","phone","telefono","name")
                    for h in rows[0]):
        rows = rows[1:]

    added = 0; skipped = 0
    with db_session() as db:
        existing_emails = {c.email for c in db.query(Contact).all() if c.email}
        existing_phones = {c.phone for c in db.query(Contact).all() if c.phone}
        batch_emails = set(); batch_phones = set()
        for row in rows:
            if not row: continue
            cols   = [c.strip() for c in row]
            nombre = cols[0] if len(cols) > 0 else ""
            email  = cols[1].lower() if len(cols) > 1 else ""
            phone  = cols[2] if len(cols) > 2 else ""
            if not email and not phone:
                skipped += 1; continue
            if phone:
                phone = _normalize_phone(phone, TWILIO_COUNTRY)
            if (email and (email in existing_emails or email in batch_emails)) or \
               (phone and (phone in existing_phones or phone in batch_phones)):
                skipped += 1; continue
            if email: batch_emails.add(email)
            if phone: batch_phones.add(phone)
            db.add(Contact(
                nombre=nombre, email=email, phone=phone,
                created=now_peru().isoformat(timespec="seconds"),
            ))
            added += 1
        db.commit()
    return jsonify({"added": added, "skipped": skipped})


# ─── WhatsApp broadcast ───────────────────────────────────────────────────────

def _normalize_phone(raw: str, default_country: str = "+51") -> str:
    """Convert a local or international phone number to E.164 format."""
    import re
    digits = re.sub(r"[^\d+]", "", (raw or "").strip())
    if not digits:
        return ""
    if digits.startswith("+"):
        return digits          # already E.164
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith("0"):
        return default_country + digits[1:]
    # Assume local number — prepend country code
    return default_country + digits

def _wa_broadcast_msg(session_dict: dict, btype: dict, url_base: str, extra_info: str = "") -> str:
    nombre_bingo = btype.get("nombre", "Bingo Pro")
    emoji        = btype.get("emoji", "🎯")
    precio       = btype.get("precio", 0.0)
    dt_raw       = session_dict.get("datetime_iso", "")
    dt_str       = dt_raw.replace("T", " ")[:16] if dt_raw else "Por confirmar"
    descripcion  = session_dict.get("descripcion", "")
    prize_pct    = btype.get("prize_pct", 0.55)
    linea_pct    = btype.get("linea_pct", 0.04)
    u_pct        = btype.get("u_pct", 0.13)
    o_pct        = btype.get("o_pct", 0.15)
    url_cartillas = f"{url_base}/cartillas"

    lines = [
        f"🎱 *¡NUEVO BINGO DISPONIBLE!*",
        f"",
        f"{emoji} *{nombre_bingo}*",
        f"📅 *Fecha:* {dt_str}",
        f"🎫 *Precio cartilla:* S/. {precio:.2f}",
    ]
    if descripcion:
        lines += [f"📝 {descripcion}"]
    if extra_info:
        lines += [f"", f"🔥 *{extra_info}*"]
    lines += [
        f"",
        f"💰 *Distribución de premios:*",
        f"  🎉 Cartón lleno o Bingo → {prize_pct*100:.0f}% mín.",
        f"  ⭕ Formar Letra O → {o_pct*100:.0f}%",
        f"  🔷 Formar Letra U → {u_pct*100:.0f}%",
        f"  ⭐ LÍNEA → {linea_pct*100:.0f}%",
        f"",
        f"🎴 *Compra tu cartilla aquí:*",
        f"{url_cartillas}",
        f"",
        f"_¡Asegura tu lugar antes de que se agoten!_ 🙌",
    ]
    return "\n".join(lines)

TWILIO_WA_TEMPLATE_SID = "HX57b3e729f20e7c5212f722d1890a1246"

def enviar_whatsapp(to_number: str, body: str, content_sid: str = None, content_variables: dict = None):
    """Send a WhatsApp message via Twilio. Returns (ok, error_str).
    If content_sid and content_variables are provided, sends a template message.
    Otherwise sends a freeform message (only valid within 24h session window).
    """
    if not TWILIO_SID or not TWILIO_TOKEN or not TWILIO_WA_FROM:
        return False, "Twilio no configurado"
    try:
        import json
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        wa_to  = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
        if content_sid and content_variables:
            client.messages.create(
                from_=TWILIO_WA_FROM,
                to=wa_to,
                content_sid=content_sid,
                content_variables=json.dumps(content_variables)
            )
        else:
            client.messages.create(from_=TWILIO_WA_FROM, to=wa_to, body=body)
        return True, ""
    except Exception as e:
        return False, str(e)

@app.route("/api/admin/session/<sid>/whatsapp_broadcast", methods=["POST"])
def api_whatsapp_broadcast_session(sid):
    """Envía mensaje de WhatsApp masivo a todos los celulares registrados."""
    chk = admin_required()
    if chk: return chk

    if not TWILIO_SID or not TWILIO_TOKEN or not TWILIO_WA_FROM:
        return jsonify({"error": "twilio_not_configured",
                        "message": "Configura TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_WHATSAPP_FROM en .env"}), 400

    s = get_session(sid)
    if not s:
        return jsonify({"error": "not found"}), 404

    data_req   = request.get_json() or {}
    extra_info = (data_req.get("extra_info") or "").strip()
    btype      = BINGO_TYPES.get(s.get("bingo_type", "1sol"), BINGO_TYPES["1sol"])
    url_base   = request.host_url.rstrip("/")
    body       = _wa_broadcast_msg(s, btype, url_base, extra_info=extra_info)

    # Collect unique phone numbers with names (players + manual contacts)
    phone_names = {}  # phone -> first name
    with db_session() as db:
        v_rows = db.query(Voucher.celular, Voucher.nombres).filter(
            Voucher.celular != "", Voucher.celular.isnot(None)
        ).all()
        c_rows = db.query(Contact.phone, Contact.nombre).filter(
            Contact.phone != "", Contact.phone.isnot(None)
        ).all()

    all_rows = [(r.celular, r.nombres) for r in v_rows] + [(r.phone, r.nombre) for r in c_rows]
    for raw, name in all_rows:
        normalized = _normalize_phone(raw or "", TWILIO_COUNTRY)
        if normalized and len(normalized) >= 8:
            if normalized not in phone_names:
                phone_names[normalized] = (name or "").split()[0] if (name or "").strip() else "amigo/a"

    dt_raw  = s.get("datetime_iso", "")
    dt_str  = dt_raw.replace("T", " ")[:16] if dt_raw else "Por confirmar"
    url_cartillas = f"{request.host_url.rstrip('/')}/cartillas"

    is_sandbox = "14155238886" in TWILIO_WA_FROM

    sent = 0; failed = 0; errors = []
    for phone, nombre in phone_names.items():
        if is_sandbox:
            ok, err = enviar_whatsapp(phone, body)
        else:
            ok, err = enviar_whatsapp(
                phone, "",
                content_sid=TWILIO_WA_TEMPLATE_SID,
                content_variables={"1": nombre, "2": dt_str, "3": url_cartillas}
            )
        if ok:
            sent += 1
        else:
            failed += 1
            if len(errors) < 5:
                errors.append(f"{phone}: {err}")

    return jsonify({"sent": sent, "failed": failed, "total": len(phone_names), "errors": errors})

@app.route("/api/admin/whatsapp/status")
def api_whatsapp_status():
    """Check if Twilio WhatsApp is configured."""
    chk = admin_required()
    if chk: return chk
    configured = bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_WA_FROM)
    return jsonify({"configured": configured, "from": TWILIO_WA_FROM if configured else ""})

@app.route("/api/admin/phones")
def api_admin_phones():
    """Count of unique phone numbers (players + contacts)."""
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        v_rows = db.query(Voucher.celular).filter(
            Voucher.celular != "", Voucher.celular.isnot(None)
        ).distinct().all()
        c_rows = db.query(Contact.phone).filter(
            Contact.phone != "", Contact.phone.isnot(None)
        ).all()
    phones = {_normalize_phone(raw, TWILIO_COUNTRY)
              for raw in [r.celular for r in v_rows] + [r.phone for r in c_rows]
              if raw and _normalize_phone(raw, TWILIO_COUNTRY)}
    return jsonify({"total": len(phones)})


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
    with db_session() as db:
        rows = db.query(BingoTypeDB).filter_by(is_active=True)\
                 .order_by(BingoTypeDB.sort_order, BingoTypeDB.id).all()
        return jsonify({"bingo_types": [r.to_dict() for r in rows]})

# ─── Admin: Bingo Types CRUD ──────────────────────────────────────────────────
@app.route("/admin/bingo_types")
def admin_bingo_types_page():
    return redirect("/admin/sessions")

@app.route("/api/admin/bingo_types", methods=["GET"])
def api_admin_list_bingo_types():
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        rows = db.query(BingoTypeDB).order_by(BingoTypeDB.sort_order, BingoTypeDB.id).all()
        return jsonify({"bingo_types": [r.to_dict() for r in rows]})

@app.route("/api/admin/bingo_types", methods=["POST"])
def api_admin_create_bingo_type():
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}

    nombre = sanitize_text(data.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "El nombre es requerido"}), 400

    try:
        precio = float(data.get("precio", 0))
    except (ValueError, TypeError):
        precio = 0
    if precio < 0:
        return jsonify({"error": "El precio no puede ser negativo"}), 400

    try:
        prize_pct = round(float(data.get("prize_pct", 0.55)), 4)
        linea_pct = round(float(data.get("linea_pct", 0.04)), 4)
        u_pct     = round(float(data.get("u_pct",     0.13)), 4)
        o_pct     = round(float(data.get("o_pct",     0.15)), 4)
    except (ValueError, TypeError):
        return jsonify({"error": "Porcentajes inválidos"}), 400

    if prize_pct + linea_pct + u_pct + o_pct > 1.0001:
        return jsonify({"error": "Los premios suman más del 100% — queda menos de 0% para la casa"}), 400

    bid = "bt_" + str(int(time.time()))
    with db_session() as db:
        max_order = db.query(BingoTypeDB).count()
        db.add(BingoTypeDB(
            id=bid,
            nombre=nombre,
            precio=precio,
            color=data.get("color", "#00e5b4"),
            emoji=data.get("emoji", "🎯"),
            descripcion=sanitize_text(data.get("descripcion", "")),
            prize_pct=prize_pct,
            linea_pct=linea_pct,
            u_pct=u_pct,
            o_pct=o_pct,
            max_cartillas=max(1, int(data.get("max_cartillas", 1))),
            is_active=bool(data.get("is_active", True)),
            is_special=bool(data.get("is_special", False)),
            special_label=sanitize_text(data.get("special_label", "")),
            sort_order=max_order + 1,
            created_at=now_peru().isoformat(),
        ))
    _reload_bingo_types()
    return jsonify({"status": "ok", "id": bid})

@app.route("/api/admin/bingo_types/<bid>", methods=["PUT"])
def api_admin_update_bingo_type(bid):
    chk = admin_required()
    if chk: return chk
    data = request.get_json() or {}

    with db_session() as db:
        bt = db.query(BingoTypeDB).filter_by(id=bid).first()
        if not bt:
            return jsonify({"error": "not found"}), 404

        if "nombre" in data:
            bt.nombre = sanitize_text(str(data["nombre"])).strip()
        if "precio" in data:
            p = float(data["precio"])
            if p < 0:
                return jsonify({"error": "El precio no puede ser negativo"}), 400
            bt.precio = p
        if "color"         in data: bt.color         = str(data["color"])
        if "emoji"         in data: bt.emoji         = str(data["emoji"])
        if "descripcion"   in data: bt.descripcion   = sanitize_text(str(data["descripcion"]))
        if "prize_pct"     in data: bt.prize_pct     = round(float(data["prize_pct"]), 4)
        if "linea_pct"     in data: bt.linea_pct     = round(float(data["linea_pct"]), 4)
        if "u_pct"         in data: bt.u_pct         = round(float(data["u_pct"]),     4)
        if "o_pct"         in data: bt.o_pct         = round(float(data["o_pct"]),     4)
        if "max_cartillas" in data: bt.max_cartillas = max(1, int(data["max_cartillas"]))
        if "is_active"     in data: bt.is_active     = bool(data["is_active"])
        if "is_special"    in data: bt.is_special    = bool(data["is_special"])
        if "special_label" in data: bt.special_label = sanitize_text(str(data["special_label"]))
        if "sort_order"    in data: bt.sort_order    = int(data["sort_order"])

        total = bt.prize_pct + bt.linea_pct + bt.u_pct + bt.o_pct
        if total > 1.0001:
            return jsonify({"error": "Los premios suman más del 100%"}), 400

    _reload_bingo_types()
    return jsonify({"status": "ok"})

@app.route("/api/admin/bingo_types/<bid>", methods=["DELETE"])
def api_admin_delete_bingo_type(bid):
    chk = admin_required()
    if chk: return chk
    with db_session() as db:
        bt = db.query(BingoTypeDB).filter_by(id=bid).first()
        if not bt:
            return jsonify({"error": "not found"}), 404
        refs = db.query(BingoSession).filter_by(bingo_type=bid).count()
        if refs > 0:
            return jsonify({"error": f"No se puede eliminar: {refs} sesión(es) ya usaron este tipo. Desactívalo en su lugar."}), 400
        db.delete(bt)
    _reload_bingo_types()
    return jsonify({"status": "ok"})

# ─── Startup ──────────────────────────────────────────────────────────────────
def _startup():
    init_db()
    _seed_bingo_types()
    _reload_bingo_types()
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
    if not is_admin() and not is_setup_pending():
        return redirect("/admin/login")
    return render_template("admin_setup_2fa.html")

@app.route("/api/admin/2fa/qr")
def api_admin_2fa_qr():
    if not is_admin() and not is_setup_pending():
        return jsonify({"error": "unauthorized"}), 401
    if not TOTP_SECRET:
        return jsonify({"error": "2fa_not_configured"}), 400
    uri = totp_provisioning_uri(TOTP_SECRET, ADMIN_USER, "Bingo Pro")
    return jsonify({"uri": uri, "secret": TOTP_SECRET})

@app.route("/api/admin/2fa/verify", methods=["POST"])
def api_admin_2fa_verify():
    if not is_admin() and not is_setup_pending():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    ok   = totp_verify(TOTP_SECRET, data.get("code", "")) if TOTP_SECRET else False
    if ok and is_setup_pending():
        # First-time enrollment: mark enrolled and upgrade to full admin session
        set_totp_enrolled(True)
        session.pop("totp_setup_pending", None)
        session["is_admin"] = True
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
    winners_limit = int(data.get("winners_limit", 99))
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
# ─── WhatsApp incoming webhook (Twilio verification) ─────────────────────────
@app.route("/api/whatsapp/incoming", methods=["POST", "GET"])
def whatsapp_incoming():
    """Webhook for incoming WhatsApp messages. Returns TwiML so Twilio
    can complete the sandbox verification handshake."""
    from flask import Response
    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Message>¡Hola! Bingo Pro recibió tu mensaje. ✅</Message></Response>'
    return Response(twiml, mimetype="text/xml")

@app.route("/admin/email_log")
def admin_email_log_page():
    chk = admin_required()
    if chk: return chk
    # Prefer file (survives restarts), fall back to in-memory
    file_entries = []
    try:
        with open(_EMAIL_LOG_FILE, encoding="utf-8") as f:
            for line in f.readlines()[-100:]:
                line = line.strip()
                if not line: continue
                parts = line.split("] [", 2)
                ts    = parts[0].lstrip("[") if len(parts) > 0 else ""
                level = parts[1] if len(parts) > 1 else "INFO"
                msg   = parts[2].rstrip("]") if len(parts) > 2 else line
                file_entries.append({"ts": ts, "level": level, "msg": msg})
        entries = list(reversed(file_entries))
    except FileNotFoundError:
        with _email_log_lock:
            entries = list(reversed(_email_log))
    rows = "".join(
        f'<tr style="border-bottom:1px solid #1a3148">'
        f'<td style="padding:8px 12px;color:#6a9ab8;white-space:nowrap">{e["ts"]}</td>'
        f'<td style="padding:8px 12px;font-weight:700;color:{"#27ae60" if e["level"]=="OK" else "#e74c3c" if e["level"] in ("ERROR","EXCEPTION") else "#f6c343"}">{e["level"]}</td>'
        f'<td style="padding:8px 12px;word-break:break-all">{e["msg"]}</td>'
        f'</tr>'
        for e in entries
    ) or '<tr><td colspan="3" style="padding:20px;color:#6a9ab8;text-align:center">Sin eventos aún — genera una cartilla para ver los logs.</td></tr>'
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Email Log — Bingo Pro</title>
<style>
body{{font-family:'Outfit',sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:20px}}
h1{{color:#00e5b4;font-size:1.6rem;margin-bottom:4px}}
p{{color:#6a9ab8;font-size:.85rem;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse;background:#0d1825;border:1px solid #1a3148;border-radius:10px;overflow:hidden}}
thead th{{padding:10px 12px;text-align:left;background:#111f2e;color:#6a9ab8;font-size:.75rem;letter-spacing:2px;text-transform:uppercase}}
a{{color:#00e5b4;text-decoration:none;font-size:.85rem}}
.refresh{{display:inline-block;margin-bottom:16px;padding:8px 16px;background:#111f2e;border:1px solid #1a3148;border-radius:8px;color:#00e5b4;cursor:pointer;font-size:.85rem;text-decoration:none}}
</style></head><body>
<a href="/admin">← Volver al Admin</a>
<h1 style="margin-top:16px">📧 Email Log</h1>
<p>Últimos {len(entries)} eventos de email (más reciente primero). <a href="/admin/email_log" class="refresh">↻ Actualizar</a></p>
<table>
  <thead><tr><th>Fecha/Hora</th><th>Estado</th><th>Mensaje</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
</body></html>"""

@app.route("/api/admin/email_log")
def api_admin_email_log():
    chk = admin_required()
    if chk: return chk
    with _email_log_lock:
        entries = list(reversed(_email_log))
    return jsonify({"log": entries})

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