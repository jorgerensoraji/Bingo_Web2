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
         bingo_types_dict, cartillas_dir_path,
         save_cartilla_fn=None, generate_grid_fn=None,
         mark_voucher_fn=None, url_base="",
         compute_prize_pool_fn=None, send_fin_emails_fn=None):
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
    _ctx["save_cartilla"]       = save_cartilla_fn
    _ctx["generate_grid"]       = generate_grid_fn
    _ctx["mark_voucher"]        = mark_voucher_fn
    _ctx["url_base"]            = url_base
    _ctx["compute_prize_pool"]  = compute_prize_pool_fn
    _ctx["send_fin_emails"]     = send_fin_emails_fn

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
    """Send winner notification emails using the proper _email_ganador template."""
    if not winners:
        return
    templates   = _ctx.get("email_templates", {})
    enviar      = _ctx.get("enviar_email")
    BINGO_TYPES = _ctx.get("BINGO_TYPES", {})
    load_vs     = _ctx.get("load_vouchers")
    vs_lock     = _ctx.get("vouchers_lock")
    ganador_tmpl = templates.get("ganador")

    if not enviar or not load_vs:
        return

    def _send():
        with vs_lock:
            all_vouchers = load_vs()

        for w in winners:
            cid     = w.get("id", "")
            voucher = next(
                (v for v in all_vouchers if cid in v.get("cartillas", [])),
                None,
            )
            email = (voucher or {}).get("email", "")
            if not email:
                continue

            btype_id = (voucher or {}).get("bingo_type", "1sol")
            btype    = BINGO_TYPES.get(btype_id, {})

            # Build winner dict with full info for the template
            winner_full = dict(w)
            winner_full["id"]        = cid
            winner_full["yape_plin"] = (voucher or {}).get("yape_plin", "")

            if ganador_tmpl:
                body   = ganador_tmpl(winner_full, btype, pattern="bingo")
                asunto = f"🏆 ¡GANASTE el BINGO! S/. {w.get('prize', 0):.2f} — Bingo Pro"
            else:
                # Fallback simple email
                body   = (f"<h2>¡Ganaste!</h2><p>Cartilla {cid} — Premio S/. {w.get('prize',0):.2f}</p>")
                asunto = "🏆 ¡GANASTE el BINGO! — Bingo Pro"

            enviar(email, asunto, body)
            _log("winner_email_sent", {"cid": cid, "email": email, "prize": w.get("prize", 0)})

    threading.Thread(target=_send, daemon=True).start()

def _auto_finish_session(session_id: str):
    """Finalize a session — mark as finished, save winners, send summary emails."""
    game      = _ctx["game"]
    game_lock = _ctx["game_lock"]
    load_ss   = _ctx["load_sessions"]
    save_ss   = _ctx["save_sessions"]
    ss_lock   = _ctx["sessions_lock"]

    with game_lock:
        winners_final = list(game.winners_log)
        drawn_final   = list(game.drawn)

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

    # Send summary email with cartilla image to every player
    send_fin = _ctx.get("send_fin_emails")
    url_base = _ctx.get("url_base", "")
    if send_fin and drawn_final:
        send_fin(session_id, drawn_final, url_base, winners_final)

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
            _tick_prepare()
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

_PREPARE_SECS = 300  # 5 minutes before game

def _tick_prepare():
    """
    5 minutes before a scheduled session:
    - Auto-generate cartillas for approved players who have none
    - Set session to 'preparing' state (5-min countdown)
    - Send email to every approved player with their personal game link (?code=)
    Only fires once per session (tracked by 'prepare_sent' flag).
    """
    load_ss  = _ctx.get("load_sessions")
    save_ss  = _ctx.get("save_sessions")
    ss_lock  = _ctx.get("sessions_lock")
    load_vs  = _ctx.get("load_vouchers")
    vs_lock  = _ctx.get("vouchers_lock")
    enviar   = _ctx.get("enviar_email")
    game     = _ctx.get("game")
    game_lock= _ctx.get("game_lock")
    save_cart= _ctx.get("save_cartilla")
    gen_grid = _ctx.get("generate_grid")
    mark_v   = _ctx.get("mark_voucher")
    tmpl_fn  = (_ctx.get("email_templates") or {}).get("aviso_inicio")
    url_base = _ctx.get("url_base", "")
    BINGO_TYPES = _ctx.get("BINGO_TYPES", {})

    if not all([load_ss, save_ss, ss_lock, load_vs, vs_lock]):
        return

    now_unix = time.time()

    with ss_lock:
        ss = load_ss()
        changed = False
        for s in ss:
            if s.get("status") != "scheduled":
                continue
            if not s.get("auto_start"):
                continue
            if s.get("prepare_sent"):
                continue

            dt_iso = s.get("datetime_iso", "9999")
            try:
                dt = datetime.fromisoformat(dt_iso).replace(tzinfo=_PERU_TZ)
                dt_unix = dt.timestamp()
            except Exception:
                continue

            # Only trigger if game starts within the next 5 minutes (and hasn't started yet)
            secs_until = dt_unix - now_unix
            if secs_until > _PREPARE_SECS or secs_until < 0:
                continue

            sid      = s["id"]
            btype_id = s.get("bingo_type", "1sol")

            s["prepare_sent"] = True
            s["status"]       = "preparing"
            s["prepare_at"]   = str(now_unix)
            s["prepare_secs"] = _PREPARE_SECS
            changed = True

            # Update in-memory game state so frontend countdown works
            with game_lock:
                game.preparing    = True
                game.prepare_at   = now_unix
                game.prepare_secs = _PREPARE_SECS
                game.prepare_sid  = sid
                if btype_id:
                    game.bingo_type = btype_id
                game.save_to_disk()

            _log("session_preparing", {"session_id": sid, "secs": _PREPARE_SECS})

            # Load approved vouchers
            with vs_lock:
                all_vs = load_vs()

            approved = [v for v in all_vs
                        if v.get("session_id") == sid
                        and v.get("payment_status") in ("approved", "manual_approved")]

            emails_to_send = []
            for v in approved:
                code      = v.get("code", "")
                cartillas = v.get("cartillas", [])

                # Auto-generate missing cartillas
                if not cartillas and code and save_cart and gen_grid and mark_v:
                    max_c  = v.get("max_cartillas") or 1
                    nombre = (v.get("nombres") or "Jugador").strip()[:40]
                    for _ in range(max_c):
                        grid = gen_grid()
                        c    = save_cart(nombre, grid, voucher_code=code,
                                         session_id=sid, bingo_type=btype_id,
                                         generada_por_admin=True)
                        mark_v(code, c["id"])
                    _log("cartillas_auto_generated", {"code": code, "count": max_c})

                if v.get("email"):
                    emails_to_send.append(v)

            # Send prep emails in background
            if enviar and tmpl_fn and emails_to_send:
                s_snap = dict(s)
                def _send(vlist=emails_to_send, snap=s_snap, ub=url_base):
                    for v in vlist:
                        code   = v.get("code", "")
                        asunto = f"⏰ ¡El bingo empieza en 5 minutos! — {snap.get('bingo_nombre', 'Bingo Pro')}"
                        body   = tmpl_fn(v, snap, ub, _PREPARE_SECS, code)
                        enviar(v["email"], asunto, body)
                        time.sleep(0.25)
                threading.Thread(target=_send, daemon=True).start()
                _log("prep_emails_queued", {"session_id": sid, "count": len(emails_to_send)})

        if changed:
            save_ss(ss)


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
            sid      = s["id"]
            btype_id = s.get("bingo_type", "1sol")

            # Use compute_prize_pool to respect premio_fijo
            cp_fn = _ctx.get("compute_prize_pool")
            if cp_fn:
                pools = cp_fn(sid, btype_id)
            else:
                btype  = BINGO_TYPES.get(btype_id, {})
                with vs_lock:
                    all_vs = load_vs()
                paid_count = len([v for v in all_vs
                    if v.get("session_id") == sid
                    and v.get("bingo_type") == btype_id
                    and v.get("payment_status") in ("approved","manual_approved")])
                gross = paid_count * btype.get("precio", 0.0)
                pools = {
                    "total_players": paid_count, "gross": gross,
                    "prize_amount":  round(gross * btype.get("prize_pct", 0.70), 2),
                    "linea_amount":  round(gross * btype.get("linea_pct", 0.10), 2),
                    "u_amount":      round(gross * btype.get("u_pct",    0.13), 2),
                    "o_amount":      round(gross * btype.get("o_pct",    0.15), 2),
                }
            paid_count = pools.get("total_players", 0)
            prize_pool = pools["prize_amount"]
            linea_pool = pools["linea_amount"]
            u_pool     = pools["u_amount"]
            o_pool     = pools["o_amount"]

            s["status"]     = "active"
            s["started_at"] = now_iso
            s["prize_info"] = {
                "total_players": pools["total_players"],
                "gross":         round(pools["gross"], 2),
                "prize_amount":  prize_pool,
                "linea_amount":  linea_pool,
                "u_amount":      u_pool,
                "o_amount":      o_pool,
            }
            changed = True

            # Reset game state
            with game_lock:
                game.reset()
                game.session_id  = sid
                game.bingo_type  = btype_id
                game.prize_pool  = prize_pool
                game.linea_pool  = linea_pool
                game.u_pool      = u_pool
                game.o_pool      = o_pool
                game.preparing   = False
                game.prepare_at  = None
                game.save_to_disk()

            # Start auto-draw for this session
            auto_cfg = s.get("auto_draw_config", {})
            configure_session(
                sid,
                interval      = auto_cfg.get("interval", 10),
                voice         = auto_cfg.get("voice", "es-PE-CamilaNeural"),
                auto_finish   = auto_cfg.get("auto_finish", True),
                winners_limit = auto_cfg.get("winners_limit", 1),
            )
            _log("session_auto_started", {"session_id": sid, "players": paid_count})

        # Also handle preparing → active when the countdown has finished
        for s in ss:
            if s.get("status") != "preparing":
                continue
            if not s.get("auto_start"):
                continue
            prepare_at   = float(s.get("prepare_at") or 0)
            prepare_secs = int(s.get("prepare_secs") or _PREPARE_SECS)
            if time.time() < prepare_at + prepare_secs:
                continue

            sid      = s["id"]
            btype_id = s.get("bingo_type", "1sol")

            cp_fn = _ctx.get("compute_prize_pool")
            if cp_fn:
                pools = cp_fn(sid, btype_id)
            else:
                btype = BINGO_TYPES.get(btype_id, {})
                with vs_lock:
                    all_vs = load_vs()
                paid_count = len([v for v in all_vs
                    if v.get("session_id") == sid
                    and v.get("bingo_type") == btype_id
                    and v.get("payment_status") in ("approved","manual_approved")])
                gross = paid_count * btype.get("precio", 0.0)
                pools = {
                    "total_players": paid_count, "gross": gross,
                    "prize_amount":  round(gross * btype.get("prize_pct", 0.70), 2),
                    "linea_amount":  round(gross * btype.get("linea_pct", 0.10), 2),
                    "u_amount":      round(gross * btype.get("u_pct",    0.13), 2),
                    "o_amount":      round(gross * btype.get("o_pct",    0.15), 2),
                }
            paid_count = pools.get("total_players", 0)
            prize_pool = pools["prize_amount"]
            linea_pool = pools["linea_amount"]
            u_pool     = pools["u_amount"]
            o_pool     = pools["o_amount"]

            s["status"]     = "active"
            s["started_at"] = now_peru().isoformat()
            s["prize_info"] = {
                "total_players": pools["total_players"],
                "gross":         round(pools["gross"], 2),
                "prize_amount":  prize_pool,
                "linea_amount":  linea_pool,
                "u_amount":      u_pool,
                "o_amount":      o_pool,
            }
            changed = True

            with game_lock:
                game.reset()
                game.session_id  = sid
                game.bingo_type  = btype_id
                game.prize_pool  = prize_pool
                game.linea_pool  = linea_pool
                game.u_pool      = u_pool
                game.o_pool      = o_pool
                game.preparing   = False
                game.prepare_at  = None
                game.save_to_disk()

            auto_cfg = s.get("auto_draw_config", {})
            configure_session(
                sid,
                interval      = auto_cfg.get("interval", 10),
                voice         = auto_cfg.get("voice", "es-PE-CamilaNeural"),
                auto_finish   = auto_cfg.get("auto_finish", True),
                winners_limit = auto_cfg.get("winners_limit", 1),
            )
            _log("session_auto_started_from_prepare", {"session_id": sid, "players": paid_count})

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