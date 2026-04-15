"""
autodraw.py — Bingo Pro v9.0
Automatic draw engine using APScheduler.

Features:
  - Sessions auto-start at their scheduled datetime
  - Balls drawn automatically at configured interval (default 15s)
  - Auto-checks ALL cartillas for winners after every draw
  - Auto-pauses when winner limit reached
  - Auto-emails winner notification
  - Auto-finishes session when all 75 balls drawn or time limit hit
  - Admin can override / take manual control at any time
  - Full audit log of every automated action
"""

import json
import logging
import random
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_PERU_TZ = ZoneInfo("America/Lima")

def now_peru() -> datetime:
    return datetime.now(_PERU_TZ).replace(tzinfo=None)

from num2words import num2words

logger = logging.getLogger("autodraw")

# ── These are injected by app.py at startup ───────────────────────────────────
# We use a module-level dict so autodraw doesn't need to import app.py
# (which would create circular imports).
_ctx = {}

def init(game_obj, game_lock_obj, load_all_cartillas_fn,
         check_winner_fn, save_sessions_fn, load_sessions_fn,
         sessions_lock_obj, load_vouchers_fn, vouchers_lock_obj,
         save_sessions_ref, enviar_email_fn, email_templates,
         bingo_types_dict, cartillas_dir_path):
    """
    Called once from app.py after all objects are initialized.
    Stores references so the scheduler can operate on live data.
    """
    _ctx["game"]              = game_obj
    _ctx["game_lock"]         = game_lock_obj
    _ctx["load_cartillas"]    = load_all_cartillas_fn
    _ctx["check_winner"]      = check_winner_fn
    _ctx["load_sessions"]     = load_sessions_fn
    _ctx["save_sessions"]     = save_sessions_fn
    _ctx["sessions_lock"]     = sessions_lock_obj
    _ctx["load_vouchers"]     = load_vouchers_fn
    _ctx["vouchers_lock"]     = vouchers_lock_obj
    _ctx["enviar_email"]      = enviar_email_fn
    _ctx["email_templates"]   = email_templates
    _ctx["BINGO_TYPES"]       = bingo_types_dict
    _ctx["CARTILLAS_DIR"]     = cartillas_dir_path

# ── Audit log ─────────────────────────────────────────────────────────────────
_audit : list = []
_audit_lock = threading.Lock()

def _log(event: str, detail: dict = None):
    entry = {
        "ts":     now_peru().isoformat(),
        "event":  event,
        "detail": detail or {},
    }
    with _audit_lock:
        _audit.append(entry)
        if len(_audit) > 500:
            _audit[:] = _audit[-400:]  # keep last 400
    logger.info(f"[AUTODRAW] {event} — {detail}")

def get_audit_log(limit: int = 50) -> list:
    with _audit_lock:
        return list(reversed(_audit[-limit:]))

# ── Scheduler state ───────────────────────────────────────────────────────────
_scheduler_thread  = None
_scheduler_running = False
_scheduler_lock    = threading.Lock()

# Per-session auto-draw configuration
# session_id -> {"interval": seconds, "voice": str, "auto_finish": bool}
_auto_config : dict = {}
_auto_lock   = threading.Lock()

def configure_session(session_id: str, interval: int = 15,
                      voice: str = "es-PE-CamilaNeural",
                      auto_finish: bool = True,
                      winners_limit: int = 1):
    """Configure auto-draw for a session."""
    with _auto_lock:
        _auto_config[session_id] = {
            "interval":     max(5, min(interval, 120)),
            "voice":        voice,
            "auto_finish":  auto_finish,
            "winners_limit": winners_limit,
            "next_draw_at": time.time() + max(5, min(interval, 120)),
            "enabled":      True,
        }
    _log("session_configured", {"session_id": session_id, "interval": interval})

def stop_session(session_id: str):
    """Stop auto-draw for a session."""
    with _auto_lock:
        cfg = _auto_config.get(session_id)
        if cfg:
            cfg["enabled"] = False
    _log("session_stopped", {"session_id": session_id})

def get_session_config(session_id: str) -> dict | None:
    with _auto_lock:
        return dict(_auto_config.get(session_id, {}))

# ── Core draw logic ───────────────────────────────────────────────────────────
def _auto_draw_one(session_id: str) -> dict | None:
    """
    Draw one ball for the active session.
    Returns draw result dict or None if can't draw.
    """
    game      = _ctx["game"]
    game_lock = _ctx["game_lock"]

    with game_lock:
        if game.session_id != session_id:
            return None
        if not game.available:
            return {"status": "finished"}
        if game.paused:
            return {"status": "paused"}

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
            phrase = letter_phrase

        game.last_phrase   = phrase
        game.last_activity = time.time()
        drawn_snapshot     = list(game.drawn)
        game.save_to_disk()

    return {
        "status": "ok",
        "number": num,
        "words":  words,
        "phrase": phrase,
        "drawn":  drawn_snapshot,
        "count":  count,
    }

def _check_all_winners(session_id: str, drawn: list) -> list:
    """
    Check all cartillas for BINGO after a draw.
    Returns list of winner dicts (may be empty).
    """
    check_fn   = _ctx["check_winner"]
    load_carts = _ctx["load_cartillas"]

    try:
        cartillas = load_carts(session_id)
    except Exception:
        return []

    game      = _ctx["game"]
    game_lock = _ctx["game_lock"]
    winners   = []

    with game_lock:
        already_claimed = set(game.claimed_winners)
        n_winners       = len(already_claimed)
        prize_pool      = game.prize_pool
        winners_limit   = game.winners_limit

    drawn_set = set(drawn)

    for c in cartillas:
        cid = c.get("id", "")
        if cid in already_claimed:
            continue
        result = check_fn(c.get("grid", []), drawn)
        if result.get("bingo"):
            winners.append({"id": cid, "nombre": c.get("nombre", ""), "cartilla": c})

    if not winners:
        return []

    # Register all new winners atomically
    with game_lock:
        if game.session_id != session_id:
            return []
        for w in winners:
            game.claimed_winners.add(w["id"])

        total_winners = len(game.claimed_winners)
        prize_each    = round(prize_pool / total_winners, 2) if total_winners else 0.0

        # Recompute existing winners' prizes (split)
        for prev in game.winners_log:
            prev["prize"]     = prize_each
            prev["n_winners"] = total_winners
            prev["split"]     = total_winners > 1

        new_entries = []
        for w in winners:
            entry = {
                "id":           w["id"],
                "nombre":       w["nombre"],
                "claimed_at":   now_peru().isoformat(),
                "drawn_count":  len(game.drawn),
                "game_id":      game.game_id,
                "prize":        prize_each,
                "n_winners":    total_winners,
                "split":        total_winners > 1,
                "auto_detected": True,
            }
            game.winners_log.append(entry)
            new_entries.append(entry)

        # Always pause when any new bingo/apagón winner is detected
        game.paused = True
        _log("game_paused", {"session_id": session_id, "winners": total_winners})

        game.save_to_disk()

    return new_entries

def _notify_winners(winners: list, session_id: str, url_base: str = ""):
    """Send winner notification emails in a background thread."""
    if not winners:
        return
    templates   = _ctx.get("email_templates", {})
    enviar      = _ctx.get("enviar_email")
    BINGO_TYPES = _ctx.get("BINGO_TYPES", {})
    load_vs     = _ctx.get("load_vouchers")
    vs_lock     = _ctx.get("vouchers_lock")

    if not enviar or not load_vs:
        return

    def _send():
        with vs_lock:
            all_vouchers = load_vs()

        for w in winners:
            cid  = w.get("id", "")
            # Find the voucher for this cartilla
            voucher = next(
                (v for v in all_vouchers if cid in v.get("cartillas", [])),
                None,
            )
            email = (voucher or {}).get("email", "")
            if not email:
                continue

            nombre    = w.get("nombre", "Jugador")
            prize     = w.get("prize", 0.0)
            n_winners = w.get("n_winners", 1)
            split_msg = (
                f" (compartido entre {n_winners} ganadores)"
                if n_winners > 1 else ""
            )

            body = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#070d14;color:#ddeeff;margin:0;padding:0">
<div style="max-width:520px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <h1 style="font-size:2.5rem;color:#f6c343;letter-spacing:3px;margin:0">🎉 ¡GANASTE!</h1>
  </div>
  <div style="background:#0d1825;border:2px solid #f6c343;border-radius:14px;padding:24px;text-align:center">
    <p style="font-size:1.1rem;margin:0 0 16px">Hola <strong style="color:#ddeeff">{nombre}</strong>,</p>
    <p style="color:#ddeeff;margin:0 0 20px">¡Tu cartilla <strong style="color:#00e5b4">{cid}</strong> ganó el BINGO!</p>
    <div style="background:#111f2e;border:2px solid #00e5b4;border-radius:12px;padding:20px;margin-bottom:16px">
      <div style="font-size:.75rem;color:#4a6b85;letter-spacing:3px;text-transform:uppercase;margin-bottom:8px">Tu premio</div>
      <div style="font-family:'Courier New',monospace;font-size:2.4rem;font-weight:900;color:#00e5b4">
        S/. {prize:.2f}
      </div>
      <div style="font-size:.78rem;color:#4a6b85;margin-top:4px">{split_msg}</div>
    </div>
    <p style="color:#4a6b85;font-size:.85rem">El organizador te contactará para coordinar el cobro de tu premio.</p>
  </div>
  <p style="text-align:center;color:#1a3148;font-size:.75rem;margin-top:20px">Bingo Pro Web v9.0</p>
</div></body></html>"""

            enviar(email, "🎉 ¡GANASTE el BINGO! — Bingo Pro", body)
            _log("winner_email_sent", {"cid": cid, "email": email, "prize": prize})

    threading.Thread(target=_send, daemon=True).start()

def _auto_finish_session(session_id: str):
    """Finalize a session — mark as finished, save winners."""
    game      = _ctx["game"]
    game_lock = _ctx["game_lock"]
    load_ss   = _ctx["load_sessions"]
    save_ss   = _ctx["save_sessions"]
    ss_lock   = _ctx["sessions_lock"]

    with game_lock:
        winners_final = list(game.winners_log)

    with ss_lock:
        ss = load_ss()
        for s in ss:
            if s.get("id") == session_id and s.get("status") == "active":
                s["status"]       = "finished"
                s["finished_at"]  = now_peru().isoformat()
                s["winners_final"]= winners_final
                s["auto_finished"]= True
                break
        save_ss(ss)

    stop_session(session_id)
    _log("session_auto_finished", {"session_id": session_id,
                                   "winners": len(winners_final)})

# ── Main scheduler loop ───────────────────────────────────────────────────────
def _scheduler_loop():
    """
    Runs in a daemon thread.
    Every second checks if any configured session needs a draw.
    Also checks if any scheduled session should auto-start.
    """
    global _scheduler_running
    logger.info("AutoDraw scheduler started")

    while _scheduler_running:
        try:
            _tick_auto_draw()
            _tick_auto_start()
        except Exception as e:
            logger.error(f"Scheduler tick error: {e}", exc_info=True)
        time.sleep(1)

    logger.info("AutoDraw scheduler stopped")

def _tick_auto_draw():
    """Check if any active session needs a ball drawn."""
    now = time.time()
    with _auto_lock:
        sessions = dict(_auto_config)

    for sid, cfg in sessions.items():
        if not cfg.get("enabled"):
            continue
        if now < cfg.get("next_draw_at", float("inf")):
            continue

        # Time to draw
        result = _auto_draw_one(sid)
        if result is None:
            continue

        # Update next draw time
        with _auto_lock:
            c = _auto_config.get(sid)
            if c:
                c["next_draw_at"] = now + c.get("interval", 15)

        if result["status"] == "finished":
            _log("all_balls_drawn", {"session_id": sid})
            _auto_finish_session(sid)
            continue

        if result["status"] == "paused":
            _log("draw_skipped_paused", {"session_id": sid})
            continue

        if result["status"] == "ok":
            _log("ball_drawn", {
                "session_id": sid,
                "number":     result["number"],
                "count":      result["count"],
            })
            # Check all cartillas for winners
            winners = _check_all_winners(sid, result["drawn"])
            if winners:
                _log("winners_detected", {"session_id": sid, "count": len(winners)})
                _notify_winners(winners, sid)
            # Auto-finish when last ball
            if result["count"] >= 75:
                _auto_finish_session(sid)

def _tick_auto_start():
    """Auto-start sessions whose datetime has arrived and have auto_start=True."""
    game      = _ctx.get("game")
    game_lock = _ctx.get("game_lock")
    load_ss   = _ctx.get("load_sessions")
    save_ss   = _ctx.get("save_sessions")
    ss_lock   = _ctx.get("sessions_lock")
    BINGO_TYPES = _ctx.get("BINGO_TYPES", {})
    load_vs   = _ctx.get("load_vouchers")
    vs_lock   = _ctx.get("vouchers_lock")

    if not all([game, game_lock, load_ss, save_ss]):
        return

    now_iso = now_peru().isoformat()

    with ss_lock:
        ss = load_ss()
        changed = False
        for s in ss:
            if s.get("status") != "scheduled":
                continue
            if not s.get("auto_start"):
                continue
            if s.get("datetime_iso", "9999") > now_iso:
                continue

            # Time to auto-start this session
            sid        = s["id"]
            btype_id   = s.get("bingo_type", "1sol")
            btype      = BINGO_TYPES.get(btype_id, {})

            # Compute prize pool
            with vs_lock:
                all_vs = load_vs()
            paid = [v for v in all_vs
                    if v.get("session_id")    == sid
                    and v.get("bingo_type")   == btype_id
                    and v.get("payment_status") in ("approved","manual_approved")]
            gross    = len(paid) * btype.get("precio", 0.0)
            prize_pool = round(gross * btype.get("prize_pct", 0.70), 2)
            linea_pool = round(gross * btype.get("linea_pct", 0.10), 2)

            s["status"]     = "active"
            s["started_at"] = now_iso
            s["prize_info"] = {
                "total_players": len(paid),
                "gross":         round(gross, 2),
                "prize_amount":  prize_pool,
                "linea_amount":  linea_pool,
            }
            changed = True

            # Reset game state
            with game_lock:
                game.reset()
                game.session_id  = sid
                game.bingo_type  = btype_id
                game.prize_pool  = prize_pool
                game.linea_pool  = linea_pool
                game.preparing   = False
                game.prepare_at  = None
                game.save_to_disk()

            # Start auto-draw for this session
            auto_cfg = s.get("auto_draw_config", {})
            configure_session(
                sid,
                interval      = auto_cfg.get("interval", 15),
                voice         = auto_cfg.get("voice", "es-PE-CamilaNeural"),
                auto_finish   = auto_cfg.get("auto_finish", True),
                winners_limit = auto_cfg.get("winners_limit", 1),
            )
            _log("session_auto_started", {"session_id": sid, "players": len(paid)})

        if changed:
            save_ss(ss)

# ── Public API ────────────────────────────────────────────────────────────────
def start_scheduler():
    """Start the background scheduler thread (idempotent)."""
    global _scheduler_thread, _scheduler_running
    with _scheduler_lock:
        if _scheduler_running:
            return
        _scheduler_running = True
        _scheduler_thread  = threading.Thread(
            target=_scheduler_loop,
            daemon=True,
            name="BingoAutoDrawScheduler",
        )
        _scheduler_thread.start()
    _log("scheduler_started", {})

def stop_scheduler():
    """Stop the background scheduler thread."""
    global _scheduler_running
    with _scheduler_lock:
        _scheduler_running = False
    _log("scheduler_stopped", {})

def is_running() -> bool:
    return _scheduler_running

def is_session_active(session_id: str) -> bool:
    """Returns True if autodraw is currently configured and enabled for this session."""
    with _auto_lock:
        return bool(_auto_config.get(session_id, {}).get("enabled", False))

def get_status() -> dict:
    with _auto_lock:
        sessions = {
            sid: {
                "enabled":      cfg.get("enabled"),
                "interval":     cfg.get("interval"),
                "next_draw_in": max(0, round(cfg.get("next_draw_at", 0) - time.time())),
            }
            for sid, cfg in _auto_config.items()
        }
    return {
        "running":  _scheduler_running,
        "sessions": sessions,
        "audit":    get_audit_log(10),
    }