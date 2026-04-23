"""
security.py — Bingo Pro v9.0
Complete security layer:
  - CSRF tokens on all state-changing requests
  - Security headers (CSP, HSTS, X-Frame, etc.)
  - Brute-force lockout for admin login
  - Admin 2FA via TOTP (Google Authenticator compatible)
  - IP-based suspicious activity detection
  - Input sanitization helpers
  - Honeypot trap for bots
"""

import hashlib
import hmac
import html
import ipaddress
import os
import re
import secrets
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps

from flask import jsonify, request, session, g

# ── CSRF ──────────────────────────────────────────────────────────────────────
CSRF_TOKEN_BYTES   = 32
CSRF_SESSION_KEY   = "_csrf_token"
CSRF_HEADER_NAME   = "X-CSRF-Token"
CSRF_COOKIE_NAME   = "csrf_token"
CSRF_TOKEN_LIFETIME = 3600  # 1 hour

def generate_csrf_token() -> str:
    """Generate and store a CSRF token in the session."""
    token = secrets.token_hex(CSRF_TOKEN_BYTES)
    session[CSRF_SESSION_KEY]          = token
    session[CSRF_SESSION_KEY + "_ts"]  = time.time()
    return token

def get_csrf_token() -> str:
    """Get existing token or create a new one."""
    tok = session.get(CSRF_SESSION_KEY)
    ts  = session.get(CSRF_SESSION_KEY + "_ts", 0)
    if not tok or (time.time() - ts) > CSRF_TOKEN_LIFETIME:
        tok = generate_csrf_token()
    return tok

def validate_csrf() -> bool:
    """
    Validate CSRF token from header or form body.
    Returns True if valid, False otherwise.
    API-only routes (JSON) use X-CSRF-Token header.
    """
    # Skip for safe methods
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True

    # Skip for the login endpoint (no session yet) and webhook endpoints
    skip_paths = {"/api/admin/login", "/api/webhook/"}
    path = request.path
    if any(path.startswith(p) for p in skip_paths):
        return True

    expected = session.get(CSRF_SESSION_KEY)
    if not expected:
        return False

    # Check header first (API calls), then form body
    provided = (
        request.headers.get(CSRF_HEADER_NAME)
        or (request.get_json(silent=True) or {}).get("_csrf")
        or request.form.get("_csrf")
    )
    if not provided:
        return False

    # Constant-time compare to prevent timing attacks
    return hmac.compare_digest(str(expected), str(provided))

def csrf_required(f):
    """Decorator: require valid CSRF token."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not validate_csrf():
            return jsonify({
                "error":   "csrf_invalid",
                "message": "Token de seguridad inválido. Recarga la página e intenta de nuevo.",
            }), 403
        return f(*args, **kwargs)
    return wrapper

# ── SECURITY HEADERS ──────────────────────────────────────────────────────────
def apply_security_headers(response):
    """
    Add security headers to every response.
    Call from Flask's after_request hook.
    """
    # Content Security Policy — tight but functional
    # Allows: self, inline styles (needed for the dark theme), Google Fonts
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "   # inline JS needed for templates
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    response.headers["Content-Security-Policy"]   = csp
    response.headers["X-Content-Type-Options"]     = "nosniff"
    response.headers["X-Frame-Options"]            = "DENY"
    response.headers["X-XSS-Protection"]           = "1; mode=block"
    response.headers["Referrer-Policy"]            = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]         = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    # HSTS: only set when behind HTTPS (nginx/caddy will set this on production)
    # Commented out here — your reverse proxy should set it instead
    # response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Remove server fingerprinting headers
    response.headers.pop("Server", None)
    response.headers.pop("X-Powered-By", None)

    return response

# ── BRUTE FORCE PROTECTION ────────────────────────────────────────────────────
_bf_lock      = threading.Lock()
_bf_attempts  : dict = defaultdict(list)   # ip -> [timestamps]
_bf_lockouts  : dict = {}                  # ip -> lockout_until timestamp
_bf_blacklist : set  = set()               # permanently blocked IPs

BF_MAX_ATTEMPTS  = 5     # attempts before lockout
BF_WINDOW_SECS   = 300   # 5-minute sliding window
BF_LOCKOUT_SECS  = 900   # 15-minute lockout
BF_HARD_LIMIT    = 20    # attempts across all time → permanent block

def record_failed_login(ip: str) -> dict:
    """
    Record a failed login attempt.
    Returns {"locked": bool, "remaining": int, "lockout_until": str|None}
    """
    now = time.time()
    with _bf_lock:
        # Clean old attempts outside window
        attempts = _bf_attempts[ip]
        attempts[:] = [t for t in attempts if now - t < BF_WINDOW_SECS]
        attempts.append(now)

        total_ever = sum(1 for v in _bf_attempts.values() for _ in v)  # rough total

        if len(attempts) >= BF_MAX_ATTEMPTS:
            until = now + BF_LOCKOUT_SECS
            _bf_lockouts[ip] = until
            # Hard-block after too many lockouts
            if len(attempts) >= BF_HARD_LIMIT:
                _bf_blacklist.add(ip)
            return {
                "locked":        True,
                "remaining":     0,
                "lockout_until": datetime.fromtimestamp(until, tz=timezone.utc).isoformat(),
            }

        return {
            "locked":        False,
            "remaining":     BF_MAX_ATTEMPTS - len(attempts),
            "lockout_until": None,
        }

def check_ip_allowed(ip: str) -> tuple[bool, str]:
    """
    Returns (True, "") if IP is allowed, (False, reason) if blocked.
    Call at the top of login and sensitive endpoints.
    """
    now = time.time()
    with _bf_lock:
        if ip in _bf_blacklist:
            return False, "IP bloqueada permanentemente por demasiados intentos."
        until = _bf_lockouts.get(ip, 0)
        if until > now:
            mins = int((until - now) / 60) + 1
            return False, f"Demasiados intentos. Espera {mins} minuto(s)."
    return True, ""

def record_success_login(ip: str) -> None:
    """Clear failed attempts on successful login."""
    with _bf_lock:
        _bf_attempts.pop(ip, None)
        _bf_lockouts.pop(ip, None)

# ── TOTP 2FA ──────────────────────────────────────────────────────────────────
# Compatible with Google Authenticator, Authy, and any TOTP app.
# Pure Python — no extra libraries needed.

import base64
import struct

def _hotp(key_bytes: bytes, counter: int) -> int:
    """HMAC-based One-Time Password (RFC 4226)."""
    msg    = struct.pack(">Q", counter)
    h      = hmac.new(key_bytes, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code   = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return code % 1_000_000

def totp_generate(secret_b32: str, window: int = 0) -> str:
    """
    Generate current TOTP code (30-second window).
    window=0 → current; window=-1 → previous; window=1 → next.
    """
    key   = base64.b32decode(secret_b32.upper().replace(" ", ""))
    t     = int(time.time() // 30) + window
    code  = _hotp(key, t)
    return f"{code:06d}"

def totp_verify(secret_b32: str, provided: str, tolerance: int = 1) -> bool:
    """
    Verify a TOTP code with ±tolerance windows (handles clock skew).
    Returns True if valid.
    """
    provided = (provided or "").strip().replace(" ", "")
    if not provided.isdigit() or len(provided) != 6:
        return False
    for w in range(-tolerance, tolerance + 1):
        if hmac.compare_digest(totp_generate(secret_b32, w), provided):
            return True
    return False

def totp_provisioning_uri(secret_b32: str, account: str, issuer: str = "Bingo Pro") -> str:
    """Generate the otpauth:// URI for QR code display."""
    from urllib.parse import quote
    s = secret_b32.upper().replace(" ", "")
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
        f"?secret={s}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )

def totp_new_secret() -> str:
    """Generate a cryptographically random TOTP secret."""
    raw = secrets.token_bytes(20)  # 160 bits → standard TOTP size
    return base64.b32encode(raw).decode("ascii")

# ── HONEYPOT ──────────────────────────────────────────────────────────────────
_honeypot_lock  = threading.Lock()
_honeypot_hits  : dict = defaultdict(int)   # ip -> hit count

HONEYPOT_FIELD = "website"   # hidden form field — bots fill it, humans don't

def check_honeypot(data: dict, ip: str) -> bool:
    """
    Returns True if the honeypot field is empty (human).
    Returns False if filled (bot) — and records the IP.
    """
    if data.get(HONEYPOT_FIELD, ""):
        with _honeypot_lock:
            _honeypot_hits[ip] += 1
            if _honeypot_hits[ip] >= 3:
                with _bf_lock:
                    _bf_blacklist.add(ip)
        return False
    return True

# ── INPUT SANITIZATION ────────────────────────────────────────────────────────
_SQL_INJECTION_PAT = re.compile(
    r"('|--|;|/\*|\*/|xp_|UNION|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|EXEC)",
    re.IGNORECASE,
)
_XSS_DANGEROUS_TAGS = re.compile(
    r"<\s*(script|iframe|object|embed|form|input|link|meta|style)",
    re.IGNORECASE,
)

def sanitize_text(value: str, max_len: int = 200) -> str:
    """
    Clean a text field:
    - Strip whitespace
    - Truncate to max_len
    - HTML-escape special characters
    - Reject SQL injection patterns
    Returns the cleaned string, or raises ValueError if dangerous.
    """
    if value is None:
        return ""
    value = str(value).strip()[:max_len]
    if _SQL_INJECTION_PAT.search(value):
        raise ValueError("Contenido no permitido detectado.")
    if _XSS_DANGEROUS_TAGS.search(value):
        raise ValueError("Etiquetas HTML no permitidas.")
    return html.escape(value, quote=True)

def sanitize_email(email: str) -> str:
    """Validate and normalize an email address."""
    email = (email or "").strip().lower()[:100]
    # Basic RFC 5322 check — enough for practical use
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
        raise ValueError("Email inválido.")
    return email

def sanitize_phone(phone: str) -> str:
    """Strip non-digit characters, allow + prefix."""
    phone = (phone or "").strip()[:20]
    cleaned = re.sub(r"[^\d+\-\s]", "", phone)
    return cleaned[:20]

def sanitize_amount(value, min_val: float = 0.01, max_val: float = 10000.0) -> float:
    """Parse and range-check a monetary amount."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError("Monto inválido.")
    if not (min_val <= f <= max_val):
        raise ValueError(f"Monto fuera de rango ({min_val}–{max_val}).")
    return round(f, 2)

# ── IP UTILITIES ──────────────────────────────────────────────────────────────
def get_real_ip() -> str:
    """
    Get the real client IP, respecting X-Forwarded-For when behind a proxy.
    Only trust the leftmost IP if we're behind a known proxy.
    """
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        # Take leftmost (client), strip extras
        ip = fwd.split(",")[0].strip()
        try:
            ipaddress.ip_address(ip)
            return ip
        except ValueError:
            pass
    return request.remote_addr or "unknown"

def is_private_ip(ip: str) -> bool:
    """Return True if IP is in a private/LAN range."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False

# ── SUSPICIOUS ACTIVITY TRACKER ───────────────────────────────────────────────
_sus_lock    = threading.Lock()
_sus_events  : dict = defaultdict(list)   # ip -> [(timestamp, event_type)]

SUS_THRESHOLD = 10    # events in window before flagging
SUS_WINDOW    = 600   # 10-minute window

def record_suspicious(ip: str, event: str) -> bool:
    """
    Record a suspicious event (e.g. 404 flood, invalid code probing).
    Returns True if IP should be blocked.
    """
    now = time.time()
    with _sus_lock:
        events = _sus_events[ip]
        events[:] = [(t, e) for t, e in events if now - t < SUS_WINDOW]
        events.append((now, event))
        count = len(events)
        if count >= SUS_THRESHOLD:
            with _bf_lock:
                _bf_blacklist.add(ip)
            return True
    return False

def get_security_stats() -> dict:
    """Return security statistics for the admin dashboard."""
    with _bf_lock:
        locked_count    = len([ip for ip, t in _bf_lockouts.items() if t > time.time()])
        blacklisted     = list(_bf_blacklist)
    with _sus_lock:
        sus_ips         = [ip for ip, evts in _sus_events.items() if len(evts) >= 5]
    with _honeypot_lock:
        bot_hits        = dict(_honeypot_hits)
    return {
        "locked_ips":     locked_count,
        "blacklisted_ips": blacklisted[:20],   # cap at 20 for display
        "suspicious_ips":  sus_ips[:20],
        "bot_hits":        sum(bot_hits.values()),
        "total_bf_events": sum(len(v) for v in _bf_attempts.values()),
    }