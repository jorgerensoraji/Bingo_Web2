#!/usr/bin/env python3
"""
BINGO PRO WEB v4.0 — Servidor Flask
Sistema completo: Juego + Cartillas + PDF + PNG + Auto-verificación
Fixed & Enhanced by Claude — v4.0
"""

import asyncio, json, os, random, socket, tempfile, threading, uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

import edge_tts
from flask import Flask, jsonify, render_template, request, send_file, session, redirect
from num2words import num2words

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas
from PIL import Image, ImageDraw, ImageFont
import qrcode

app = Flask(__name__)

# ─── Seguridad ────────────────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
ADMIN_USER = os.environ.get("ADMIN_USER", "jorgerensoraji")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "Humildes1!@#$%")

def is_admin() -> bool:
    return bool(session.get("is_admin"))

def admin_required():
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401
    return None

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
CARTILLAS_DIR = BASE_DIR / "cartillas_data"
TTS_DIR       = Path(tempfile.gettempdir()) / "bingo_web_tts"
CARTILLAS_DIR.mkdir(exist_ok=True)
TTS_DIR.mkdir(exist_ok=True)

# ─── Vouchers ────────────────────────────────────────────────────────────────
VOUCHERS_FILE = CARTILLAS_DIR / "_vouchers.json"
vouchers_lock = threading.Lock()

def _load_vouchers() -> list:
    if not VOUCHERS_FILE.exists():
        return []
    try:
        return json.loads(VOUCHERS_FILE.read_text(encoding="utf-8"))
    except:
        return []

def _save_vouchers(vs: list) -> None:
    VOUCHERS_FILE.write_text(json.dumps(vs, ensure_ascii=False, indent=2), encoding="utf-8")

def _gen_voucher_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(6))

def _find_voucher(vs: list, code: str):
    code = (code or "").strip().upper()
    for v in vs:
        if v.get("code", "").upper() == code:
            return v
    return None

def validate_voucher_code(code: str) -> tuple:
    code = (code or "").strip().upper()
    if not code:
        return False, "bad_code"
    with vouchers_lock:
        vs = _load_vouchers()
        v = _find_voucher(vs, code)
        if not v:
            return False, "bad_code"
        if v.get("used"):
            return False, "used_code"
    return True, ""

def mark_voucher_used(code: str, cartilla_ids: list) -> None:
    code = (code or "").strip().upper()
    with vouchers_lock:
        vs = _load_vouchers()
        v = _find_voucher(vs, code)
        if not v:
            return
        v["used"]     = True
        v["used_at"]  = datetime.now().isoformat()
        v["cartillas"] = cartilla_ids
        _save_vouchers(vs)

# ─── Estado del juego ─────────────────────────────────────────────────────────
class GameState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.available = list(range(1, 91))
        self.drawn: list = []
        self.last = None

    def draw(self):
        if not self.available:
            return None
        num = random.choice(self.available)
        self.available.remove(num)
        self.drawn.append(num)
        self.last = num
        return num

game      = GameState()
game_lock = threading.Lock()

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
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# ─── Generador de cartillas ───────────────────────────────────────────────────
COL_RANGES = [
    list(range(1,  10)),
    list(range(10, 20)),
    list(range(20, 30)),
    list(range(30, 40)),
    list(range(40, 50)),
    list(range(50, 60)),
    list(range(60, 70)),
    list(range(70, 80)),
    list(range(80, 91)),
]

def generate_cartilla_grid():
    for _ in range(1000):
        col_counts = [1] * 9
        extras = random.sample(range(9), 6)
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

def save_cartilla(nombre: str, grid: list) -> dict:
    cid  = str(uuid.uuid4())[:8].upper()
    data = {
        "id":      cid,
        "nombre":  nombre,
        "grid":    grid,
        "created": datetime.now().isoformat(),
    }
    (CARTILLAS_DIR / f"{cid}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return data

def load_all_cartillas() -> list:
    cartillas = []
    for f in sorted(CARTILLAS_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            cartillas.append(json.loads(f.read_text(encoding="utf-8")))
        except:
            pass
    return cartillas

def load_cartilla(cid: str):
    f = CARTILLAS_DIR / f"{cid}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))

def check_winner(grid: list, drawn: list) -> dict:
    drawn_set = set(drawn)
    nums      = [n for row in grid for n in row if n is not None]
    marked    = [n for n in nums if n in drawn_set]
    result    = {
        "total":     len(nums),
        "marked":    len(marked),
        "bingo":     len(marked) == len(nums),
        "linea":     False,
        "linea_row": None,
    }
    for i, row in enumerate(grid):
        row_nums = [n for n in row if n is not None]
        if row_nums and all(n in drawn_set for n in row_nums):
            result["linea"]     = True
            result["linea_row"] = i
            break
    return result

# ─── Font helper (cross-platform) ────────────────────────────────────────────
def _get_font(bold=False, size=16):
    """Try to find a usable font on any OS."""
    candidates_bold   = ["arialbd.ttf", "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
    candidates_normal = ["arial.ttf",   "DejaVuSans.ttf",      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    candidates = candidates_bold if bold else candidates_normal
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except:
            pass
    return ImageFont.load_default()

# ─── PDF ──────────────────────────────────────────────────────────────────────
GROUP_COLORS_HEX = [
    "#5dade2","#f4d03f","#f1948a","#e59866",
    "#58d68d","#a569bd","#48c9b0","#7fb3d3","#95a5a6",
]

def hex_to_rgb01(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))

def cartilla_to_pdf(cartilla: dict, drawn: list = None) -> BytesIO:
    drawn_set = set(drawn or [])
    buf = BytesIO()
    W, H = A4
    c = rl_canvas.Canvas(buf, pagesize=A4)
    cw, ch = W, H

    c.setFillColorRGB(0.04, 0.07, 0.10)
    c.rect(0, 0, cw, ch, fill=1, stroke=0)

    c.setFillColorRGB(0, 0.85, 0.70)
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(cw / 2, ch - 2.2 * cm, "BINGO PRO")

    c.setFillColorRGB(0.55, 0.70, 0.80)
    c.setFont("Helvetica", 12)
    c.drawCentredString(cw / 2, ch - 3.0 * cm,
                        f"Cartilla #{cartilla['id']}  —  {cartilla['nombre']}")

    c.setFillColorRGB(0.3, 0.5, 0.6)
    c.setFont("Helvetica", 9)
    c.drawCentredString(cw / 2, ch - 3.6 * cm,
                        f"Generada: {cartilla['created'][:16].replace('T', ' ')}")

    grid  = cartilla["grid"]
    col_w = (cw - 4 * cm) / 9
    row_h = 1.8 * cm
    x0    = 2 * cm
    y0    = ch - 5.5 * cm

    col_labels = ["1-9","10-19","20-29","30-39","40-49","50-59","60-69","70-79","80-90"]
    for ci in range(9):
        r, g, b = hex_to_rgb01(GROUP_COLORS_HEX[ci])
        cx = x0 + ci * col_w
        c.setFillColorRGB(r * 0.3, g * 0.3, b * 0.3)
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
            r2,g2,b2 = hex_to_rgb01(GROUP_COLORS_HEX[ci])

            if num is None:
                c.setFillColorRGB(0.07, 0.12, 0.16)
                c.setStrokeColorRGB(0.10, 0.18, 0.24)
            elif num in drawn_set:
                c.setFillColorRGB(r2 * 0.5, g2 * 0.5, b2 * 0.5)
                c.setStrokeColorRGB(r2, g2, b2)
            else:
                c.setFillColorRGB(0.07, 0.16, 0.22)
                c.setStrokeColorRGB(0.15, 0.28, 0.38)

            c.roundRect(cx + 2, cy - row_h + 4, col_w - 4, row_h - 6, 6, fill=1, stroke=1)

            if num is not None:
                if num in drawn_set:
                    c.setFillColorRGB(r2, g2, b2)
                    r_circ = min(col_w, row_h) * 0.36
                    c.circle(cx + col_w / 2, cy - row_h / 2 + 2, r_circ, fill=1, stroke=0)
                    c.setFillColorRGB(0.04, 0.07, 0.10)
                else:
                    c.setFillColorRGB(r2 * 1.2, g2 * 1.2, b2 * 1.2)
                c.setFont("Helvetica-Bold", 18)
                c.drawCentredString(cx + col_w / 2, cy - row_h / 2 - 5, str(num))

    # QR
    qr     = qrcode.make(f"BINGO-{cartilla['id']}")
    qr_buf = BytesIO()
    qr.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_size = 2.5 * cm
    c.drawImage(qr_buf, cw - 3 * cm, 1.5 * cm,
                width=qr_size, height=qr_size,
                preserveAspectRatio=True, mask='auto')
    c.setFillColorRGB(0.3, 0.5, 0.6)
    c.setFont("Helvetica", 7)
    c.drawCentredString(cw - 3 * cm + qr_size / 2, 1.2 * cm, f"ID: {cartilla['id']}")

    marked_count = len([n for row in grid for n in row if n and n in drawn_set])
    c.setFillColorRGB(0.25, 0.40, 0.52)
    c.setFont("Helvetica", 9)
    c.drawCentredString(cw / 2, 2.0 * cm, "Made by Renso Ramirez  •  Bingo Pro Web v4.0")
    c.drawCentredString(cw / 2, 1.4 * cm, f"Numeros marcados: {marked_count} / 15")

    c.save()
    buf.seek(0)
    return buf

def cartilla_to_png(cartilla: dict, drawn: list = None) -> BytesIO:
    drawn_set = set(drawn or [])
    grid = cartilla["grid"]

    COLS, ROWS = 9, 3
    PAD        = 30
    HEADER_H   = 110
    FOOTER_H   = 60
    CELL_W, CELL_H = 90, 80
    W = PAD * 2 + COLS * CELL_W
    H = PAD * 2 + HEADER_H + ROWS * CELL_H + FOOTER_H

    img  = Image.new("RGB", (W, H), (10, 18, 26))
    draw = ImageDraw.Draw(img)

    font_big   = _get_font(bold=True,  size=32)
    font_med   = _get_font(bold=False, size=16)
    font_small = _get_font(bold=False, size=12)
    font_num   = _get_font(bold=True,  size=26)
    font_col   = _get_font(bold=True,  size=11)

    draw.text((W // 2, PAD + 8),  "BINGO PRO",
              fill=(0, 210, 170), anchor="mt", font=font_big)
    draw.text((W // 2, PAD + 50), f"Cartilla #{cartilla['id']}  —  {cartilla['nombre']}",
              fill=(140, 180, 210), anchor="mt", font=font_med)
    draw.text((W // 2, PAD + 76), f"Generada: {cartilla['created'][:16].replace('T', ' ')}",
              fill=(80, 110, 130), anchor="mt", font=font_small)

    GROUP_RGB = [
        (93,173,226),(244,208,63),(241,148,138),(229,152,102),
        (88,214,141),(165,105,189),(72,201,176),(127,179,211),(149,165,166),
    ]
    col_labels = ["1-9","10-19","20-29","30-39","40-49","50-59","60-69","70-79","80-90"]
    y_start = PAD + HEADER_H

    for ci in range(COLS):
        cx    = PAD + ci * CELL_W
        r,g,b = GROUP_RGB[ci]
        draw.rounded_rectangle(
            [cx + 3, y_start - 26, cx + CELL_W - 3, y_start - 4],
            radius=6,
            fill=(int(r * .25), int(g * .25), int(b * .25))
        )
        draw.text((cx + CELL_W // 2, y_start - 15), col_labels[ci],
                  fill=(r, g, b), anchor="mm", font=font_col)

    for ri in range(ROWS):
        for ci in range(COLS):
            num   = grid[ri][ci]
            cx    = PAD + ci * CELL_W
            cy    = y_start + ri * CELL_H
            r,g,b = GROUP_RGB[ci]

            if num is None:
                cell_fill   = (12, 22, 32)
                border_fill = (20, 40, 55)
            elif num in drawn_set:
                cell_fill   = (int(r * .45), int(g * .45), int(b * .45))
                border_fill = (r, g, b)
            else:
                cell_fill   = (14, 32, 46)
                border_fill = (30, 65, 90)

            draw.rounded_rectangle(
                [cx + 4, cy + 4, cx + CELL_W - 4, cy + CELL_H - 4],
                radius=10, fill=cell_fill, outline=border_fill, width=2
            )

            if num is not None:
                if num in drawn_set:
                    margin = 12
                    draw.ellipse(
                        [cx + margin, cy + margin, cx + CELL_W - margin, cy + CELL_H - margin],
                        fill=(r, g, b)
                    )
                    txt_color = (15, 25, 35)
                else:
                    txt_color = (min(255, int(r * 1.1)), min(255, int(g * 1.1)), min(255, int(b * 1.1)))

                draw.text((cx + CELL_W // 2, cy + CELL_H // 2),
                          str(num), fill=txt_color, anchor="mm", font=font_num)

    fy = y_start + ROWS * CELL_H + 12
    draw.text((W // 2, fy),     f"ID: {cartilla['id']}",
              fill=(100, 150, 180), anchor="mt", font=_get_font(size=13))
    draw.text((W // 2, fy + 24), "Made by Renso Ramirez  •  Bingo Pro Web v4.0",
              fill=(60, 90, 110), anchor="mt", font=_get_font(size=11))

    buf = BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    buf.seek(0)
    return buf

# ─── Páginas ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", is_admin=is_admin())

@app.route("/cartillas")
def cartillas_player_page():
    return render_template("cartillas_player.html")

@app.route("/admin/login")
def admin_login_page():
    if is_admin():
        return redirect("/admin")
    return render_template("admin_login.html")

@app.route("/admin")
def admin_page():
    if not is_admin():
        return redirect("/admin/login")
    return render_template("admin.html")

@app.route("/admin/cartillas")
def admin_cartillas_page():
    if not is_admin():
        return redirect("/admin/login")
    return render_template("cartillas_admin.html")

# ─── API Admin: Auth ──────────────────────────────────────────────────────────
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data     = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")
    if username == ADMIN_USER and password == ADMIN_PASS:
        session["is_admin"]   = True
        session["admin_user"] = username
        return jsonify({"status": "ok"})
    return jsonify({"error": "invalid_credentials"}), 401

@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.clear()
    return jsonify({"status": "ok"})

# ─── API Juego ────────────────────────────────────────────────────────────────
@app.route("/api/draw", methods=["POST"])
def api_draw():
    chk = admin_required()
    if chk: return chk

    with game_lock:
        if not game.available:
            return jsonify({"status": "finished", "drawn": game.drawn})
        num   = game.draw()
        words = num2words(num, lang="es")
        count = len(game.drawn)

        if count == 1:
            phrase = f"Primera bolilla, número {words}"
        elif count == 90:
            phrase = f"Última bolilla, número {words}. Juego completo!"
        else:
            phrase = f"La siguiente bolilla es el número {words}"

        return jsonify({
            "status":    "ok",
            "number":    num,
            "words":     words,
            "phrase":    phrase,
            "drawn":     game.drawn,
            "remaining": len(game.available),
            "count":     count,
        })

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
        words  = num2words(game.last, lang="es")
        phrase = f"Repito, bolilla número {words}"

    try:
        return send_file(make_audio(phrase, voice), mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/reset", methods=["POST"])
def api_reset():
    chk = admin_required()
    if chk: return chk
    with game_lock:
        game.reset()
    return jsonify({"status": "ok"})

@app.route("/api/state")
def api_state():
    with game_lock:
        return jsonify({
            "drawn":     game.drawn,
            "remaining": len(game.available),
            "last":      game.last
        })

# ─── API Admin: Vouchers ──────────────────────────────────────────────────────
@app.route("/api/admin/voucher", methods=["POST"])
def api_admin_create_voucher():
    chk = admin_required()
    if chk: return chk

    data      = request.get_json() or {}
    numero    = (data.get("numero")    or "").strip()[:30]
    nombres   = (data.get("nombres")   or "").strip()[:60]
    apellidos = (data.get("apellidos") or "").strip()[:60]

    with vouchers_lock:
        vs   = _load_vouchers()
        code = _gen_voucher_code()
        while _find_voucher(vs, code):
            code = _gen_voucher_code()
        v = {
            "code":      code,
            "numero":    numero,
            "nombres":   nombres,
            "apellidos": apellidos,
            "created":   datetime.now().isoformat(),
            "used":      False,
        }
        vs.insert(0, v)
        _save_vouchers(vs)

    return jsonify({"status": "ok", "voucher": v})

@app.route("/api/admin/vouchers")
def api_admin_list_vouchers():
    chk = admin_required()
    if chk: return chk
    with vouchers_lock:
        vs = _load_vouchers()
    return jsonify({"vouchers": vs})

@app.route("/api/admin/voucher/<code>/delete", methods=["DELETE"])
def api_admin_delete_voucher(code):
    chk = admin_required()
    if chk: return chk
    code = code.strip().upper()
    with vouchers_lock:
        vs = _load_vouchers()
        vs = [v for v in vs if v.get("code", "").upper() != code]
        _save_vouchers(vs)
    return jsonify({"status": "ok"})

@app.route("/api/voucher/check", methods=["POST"])
def api_voucher_check():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    ok, err = validate_voucher_code(code)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})

# ─── API Cartillas ────────────────────────────────────────────────────────────
@app.route("/api/cartilla/save_manual", methods=["POST"])
def api_save_manual():
    data   = request.get_json() or {}
    nombre = (data.get("nombre", "") or "Jugador").strip()[:40]
    code   = (data.get("code",   "") or "").strip().upper()
    grid   = data.get("grid")

    # Admin bypass — admins can always save without voucher
    if not is_admin():
        with game_lock:
            if len(game.drawn) > 0:
                return jsonify({"error": "game_started"}), 403

        ok, err = validate_voucher_code(code)
        if not ok:
            return jsonify({"error": err}), 403

    if not grid or len(grid) != 3 or any(len(r) != 9 for r in grid):
        return jsonify({"error": "grid invalido"}), 400

    nums = [n for row in grid for n in row if n is not None]
    if len(nums) != 15:
        return jsonify({"error": f"Se requieren 15 numeros, recibidos: {len(nums)}"}), 400

    cartilla = save_cartilla(nombre, grid)

    if not is_admin() and code:
        mark_voucher_used(code, [cartilla["id"]])

    return jsonify({"status": "ok", "cartilla": cartilla})

@app.route("/api/cartilla/generate", methods=["POST"])
def api_generate():
    data   = request.get_json() or {}
    nombre = (data.get("nombre", "") or "Jugador").strip()[:40]
    code   = (data.get("code",   "") or "").strip().upper()
    count  = min(int(data.get("count", 1)), 20)

    # Admin bypass — admins can generate without voucher
    if not is_admin():
        with game_lock:
            if len(game.drawn) > 0:
                return jsonify({"error": "game_started"}), 403

        ok, err = validate_voucher_code(code)
        if not ok:
            return jsonify({"error": err}), 403

    results = []
    for _ in range(count):
        grid     = generate_cartilla_grid()
        cartilla = save_cartilla(nombre, grid)
        results.append(cartilla)

    if not is_admin() and code:
        mark_voucher_used(code, [c["id"] for c in results])

    return jsonify({"status": "ok", "cartillas": results})

@app.route("/api/cartilla/list")
def api_list():
    return jsonify({"cartillas": load_all_cartillas()})

@app.route("/api/cartilla/<cid>")
def api_get(cid):
    c = load_cartilla(cid.upper())
    if not c:
        return jsonify({"error": "not found"}), 404
    return jsonify(c)

@app.route("/api/cartilla/<cid>/check")
def api_check(cid):
    c = load_cartilla(cid.upper())
    if not c:
        return jsonify({"error": "not found"}), 404
    with game_lock:
        drawn2 = list(game.drawn)
    result = check_winner(c["grid"], drawn2)
    result["id"]     = c["id"]
    result["nombre"] = c["nombre"]
    return jsonify(result)

@app.route("/api/cartilla/check_all")
def api_check_all():
    """Check all cartillas against current drawn numbers — used by admin panel."""
    with game_lock:
        drawn2 = list(game.drawn)

    cartillas = load_all_cartillas()
    results   = []
    for c in cartillas:
        r         = check_winner(c["grid"], drawn2)
        r["id"]     = c["id"]
        r["nombre"] = c["nombre"]
        results.append(r)

    return jsonify({
        "results":     results,
        "drawn_count": len(drawn2),
    })

@app.route("/api/cartilla/<cid>/pdf")
def api_pdf(cid):
    c = load_cartilla(cid.upper())
    if not c:
        return jsonify({"error": "not found"}), 404
    with game_lock:
        drawn2 = list(game.drawn)
    buf  = cartilla_to_pdf(c, drawn2)
    name = f"cartilla_{cid}_{c['nombre'].replace(' ','_')}.pdf"
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name=name)

@app.route("/api/cartilla/<cid>/png")
def api_png(cid):
    c = load_cartilla(cid.upper())
    if not c:
        return jsonify({"error": "not found"}), 404
    with game_lock:
        drawn2 = list(game.drawn)
    buf  = cartilla_to_png(c, drawn2)
    name = f"cartilla_{cid}_{c['nombre'].replace(' ','_')}.png"
    return send_file(buf, mimetype="image/png",
                     as_attachment=True, download_name=name)

@app.route("/api/cartilla/<cid>/delete", methods=["DELETE"])
def api_delete_cartilla(cid):
    chk = admin_required()
    if chk: return chk
    f = CARTILLAS_DIR / f"{cid.upper()}.json"
    if f.exists():
        f.unlink()
        return jsonify({"status": "ok"})
    return jsonify({"error": "not found"}), 404

@app.route("/api/cartilla/delete_all", methods=["DELETE"])
def api_delete_all_cartillas():
    chk = admin_required()
    if chk: return chk
    count = 0
    for f in CARTILLAS_DIR.glob("*.json"):
        if not f.name.startswith("_"):
            f.unlink()
            count += 1
    return jsonify({"status": "ok", "deleted": count})

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ip = get_local_ip()
    print("\n" + "═" * 52)
    print("  BINGO PRO WEB v4.0")
    print("═" * 52)
    print(f"  Red WiFi  ->  http://{ip}:5000")
    print(f"  Esta PC   ->  http://localhost:5000")
    print(f"  Cartillas ->  http://{ip}:5000/cartillas")
    print(f"  Admin     ->  http://{ip}:5000/admin")
    print("═" * 52 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
