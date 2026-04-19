"""
models.py — Bingo Pro v8.0
SQLAlchemy ORM models replacing the JSON file system.
"""

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, Text, Index, BigInteger
)
from database import Base


class Voucher(Base):
    __tablename__ = "vouchers"

    code                 = Column(String(10),  primary_key=True)
    nombres              = Column(String(60),  default="")
    apellidos            = Column(String(60),  default="")
    email                = Column(String(100), default="")
    celular              = Column(String(20),  default="")
    bingo_type           = Column(String(20),  default="1sol")
    bingo_nombre         = Column(String(60),  default="")
    precio               = Column(Float,       default=0.0)
    session_id           = Column(String(20),  default="", index=True)
    payment_method       = Column(String(30),  default="")
    payment_ref          = Column(String(80),  default="")
    payment_status       = Column(String(30),  default="pending", index=True)
    payment_submitted_at = Column(String(30),  nullable=True)
    approved_at          = Column(String(30),  nullable=True)
    approved_note        = Column(String(200), default="")
    rejected_at          = Column(String(30),  nullable=True)
    rejected_reason      = Column(String(200), default="")
    yape_plin            = Column(String(20),  default="")
    terms_accepted       = Column(Boolean,     default=False)
    email_codigo_enviado = Column(Boolean,     default=False)
    email_enviado_at     = Column(String(30),  nullable=True)
    email_reenviado_at   = Column(String(30),  nullable=True)
    access_pin           = Column(String(64),  default="")   # SHA-256 del PIN de 4 dígitos
    pin_hint             = Column(String(4),   default="")   # PIN en texto plano (solo para email de aprobación)
    creado_por           = Column(String(20),  default="admin")
    referral_code_used   = Column(String(12),  default="")
    # JSON-encoded list of cartilla IDs  e.g. '["AB12", "CD34"]'
    cartillas_ids        = Column(Text,        default="[]")
    max_cartillas        = Column(Integer,     default=1)
    created              = Column(String(30),  default="")

    # Indexes for common query patterns
    __table_args__ = (
        Index("ix_vouchers_email",      "email"),
        Index("ix_vouchers_session_status", "session_id", "payment_status"),
    )

    def cartillas_list(self) -> list:
        import json
        try:
            return json.loads(self.cartillas_ids or "[]")
        except Exception:
            return []

    def to_dict(self) -> dict:
        import json
        return {
            "code":                   self.code,
            "nombres":                self.nombres,
            "apellidos":              self.apellidos,
            "email":                  self.email,
            "celular":                self.celular,
            "bingo_type":             self.bingo_type,
            "bingo_nombre":           self.bingo_nombre,
            "precio":                 self.precio,
            "session_id":             self.session_id,
            "payment_method":         self.payment_method,
            "payment_ref":            self.payment_ref,
            "yape_plin":              self.yape_plin or "",
            "terms_accepted":         bool(self.terms_accepted),
            "payment_status":         self.payment_status,
            "payment_submitted_at":   self.payment_submitted_at,
            "approved_at":            self.approved_at,
            "approved_note":          self.approved_note,
            "rejected_at":            self.rejected_at,
            "rejected_reason":        self.rejected_reason,
            "email_codigo_enviado":   self.email_codigo_enviado,
            "email_enviado_at":       self.email_enviado_at,
            "email_reenviado_at":     self.email_reenviado_at,
            "creado_por":             self.creado_por,
            "referral_code_used":     self.referral_code_used or "",
            "pin_hint":               self.pin_hint or "",
            "cartillas":              self.cartillas_list(),
            "max_cartillas":          self.max_cartillas or 1,
            "created":                self.created,
            "has_pin":                bool(self.access_pin),
        }


class Session(Base):
    __tablename__ = "sessions"

    id            = Column(String(20),  primary_key=True)
    bingo_type    = Column(String(20),  default="1sol")
    bingo_nombre  = Column(String(60),  default="")
    bingo_color   = Column(String(10),  default="")
    bingo_precio  = Column(Float,       default=0.0)
    datetime_iso  = Column(String(30),  default="", index=True)
    date          = Column(String(12),  default="")
    time          = Column(String(6),   default="")
    descripcion   = Column(String(300), default="")
    max_players   = Column(Integer,     default=0)
    status        = Column(String(20),  default="scheduled", index=True)
    created       = Column(String(30),  default="")
    started_at    = Column(String(30),  nullable=True)
    finished_at   = Column(String(30),  nullable=True)
    prepare_at    = Column(String(30),  nullable=True)
    prepare_secs  = Column(Integer,     default=60)
    premio_fijo   = Column(Float,       default=0.0)
    # JSON blobs
    prize_info    = Column(Text,        default="{}")
    winners_final = Column(Text,        default="[]")

    def to_dict(self) -> dict:
        import json
        pi = {}
        try:
            pi = json.loads(self.prize_info or "{}")
        except Exception:
            pass
        wf = []
        try:
            wf = json.loads(self.winners_final or "[]")
        except Exception:
            pass
        return {
            "id":            self.id,
            "bingo_type":    self.bingo_type,
            "bingo_nombre":  self.bingo_nombre,
            "bingo_color":   self.bingo_color,
            "bingo_precio":  self.bingo_precio,
            "datetime_iso":  self.datetime_iso,
            "date":          self.date,
            "time":          self.time,
            "descripcion":   self.descripcion,
            "max_players":   self.max_players,
            "status":        self.status,
            "created":       self.created,
            "started_at":    self.started_at,
            "finished_at":   self.finished_at,
            "prepare_at":    self.prepare_at,
            "prepare_secs":  self.prepare_secs,
            "premio_fijo":   self.premio_fijo or 0.0,
            "prize_info":    pi,
            "winners_final": wf,
        }


class Cartilla(Base):
    __tablename__ = "cartillas"

    id                 = Column(String(10),  primary_key=True)
    nombre             = Column(String(60),  default="")
    telefono           = Column(String(30),  default="")
    voucher_code       = Column(String(10),  default="", index=True)
    session_id         = Column(String(20),  default="", index=True)
    bingo_type         = Column(String(20),  default="1sol")
    # JSON-encoded 3×9 grid  e.g. '[[1,null,...],[...],[...]]'
    grid               = Column(Text,        default="[]")
    created            = Column(String(30),  default="")
    generada_por_admin = Column(Boolean,     default=False)

    def grid_data(self) -> list:
        import json
        try:
            return json.loads(self.grid or "[]")
        except Exception:
            return []

    def to_dict(self) -> dict:
        return {
            "id":                 self.id,
            "nombre":             self.nombre,
            "telefono":           self.telefono,
            "voucher_code":       self.voucher_code,
            "session_id":         self.session_id,
            "bingo_type":         self.bingo_type,
            "grid":               self.grid_data(),
            "created":            self.created,
            "generada_por_admin": self.generada_por_admin,
        }


class Config(Base):
    """Single-row key/value config store. key='main' holds the full config JSON."""
    __tablename__ = "config"

    key   = Column(String(50), primary_key=True)
    value = Column(Text, default="{}")


class BingoTypeDB(Base):
    """Dynamic bingo types — created and managed from the admin panel."""
    __tablename__ = "bingo_types"

    id            = Column(String(30),  primary_key=True)
    nombre        = Column(String(60),  default="")
    precio        = Column(Float,       default=0.0)
    color         = Column(String(10),  default="#f472b6")
    emoji         = Column(String(10),  default="🎯")
    descripcion   = Column(String(200), default="")
    prize_pct     = Column(Float,       default=0.55)   # BINGO full card
    linea_pct     = Column(Float,       default=0.04)   # LINEA any row
    u_pct         = Column(Float,       default=0.13)   # U pattern
    o_pct         = Column(Float,       default=0.15)   # O pattern
    # house_pct = 1 - prize - linea - u - o  (auto-calculated, never stored)
    max_cartillas = Column(Integer,     default=1)
    is_active     = Column(Boolean,     default=True)
    is_special    = Column(Boolean,     default=False)
    special_label = Column(String(100), default="")
    sort_order    = Column(Integer,     default=0)
    created_at    = Column(String(30),  default="")

    def to_dict(self) -> dict:
        import json as _json
        house = round(max(0.0, 1.0 - self.prize_pct - self.linea_pct - self.u_pct - self.o_pct), 4)
        return {
            "id":                        self.id,
            "nombre":                    self.nombre,
            "precio":                    self.precio,
            "color":                     self.color,
            "emoji":                     self.emoji,
            "descripcion":               self.descripcion,
            "prize_pct":                 self.prize_pct,
            "linea_pct":                 self.linea_pct,
            "u_pct":                     self.u_pct,
            "o_pct":                     self.o_pct,
            "house_pct":                 house,
            "max_cartillas":             self.max_cartillas,
            "max_cartillas_per_voucher": self.max_cartillas,   # backend compat alias
            "is_active":                 bool(self.is_active),
            "is_special":                bool(self.is_special),
            "special_label":             self.special_label or "",
            "sort_order":                self.sort_order,
            "balls":                     75,
            "grid_type":                 "75",
        }


class Reimbursement(Base):
    """Players owed a refund when a session is cancelled or deleted."""
    __tablename__ = "reimbursements"

    id              = Column(Integer,     primary_key=True, autoincrement=True)
    session_id      = Column(String(20),  default="", index=True)
    session_nombre  = Column(String(60),  default="")
    voucher_code    = Column(String(10),  default="")
    nombre          = Column(String(60),  default="")
    apellidos       = Column(String(60),  default="")
    email           = Column(String(100), default="")
    celular         = Column(String(20),  default="")
    yape_plin       = Column(String(20),  default="")
    amount          = Column(Float,       default=0.0)
    status          = Column(String(20),  default="pending")  # pending / paid
    created         = Column(String(30),  default="")
    paid_at         = Column(String(30),  default="")
    paid_ref        = Column(String(80),  default="")
    paid_note       = Column(String(200), default="")

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "session_id":     self.session_id    or "",
            "session_nombre": self.session_nombre or "",
            "voucher_code":   self.voucher_code  or "",
            "nombre":         self.nombre        or "",
            "apellidos":      self.apellidos     or "",
            "email":          self.email         or "",
            "celular":        self.celular        or "",
            "yape_plin":      self.yape_plin      or "",
            "amount":         self.amount         or 0.0,
            "status":         self.status         or "pending",
            "created":        self.created        or "",
            "paid_at":        self.paid_at        or "",
            "paid_ref":       self.paid_ref       or "",
            "paid_note":      self.paid_note      or "",
        }


class Contact(Base):
    """Manual contacts for broadcast — people not yet in the Voucher table."""
    __tablename__ = "contacts"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    nombre        = Column(String(120), default="")
    email         = Column(String(100), default="")
    phone         = Column(String(25),  default="")   # E.164 format e.g. +51987654321
    referral_code = Column(String(12),  default="", index=True)
    created       = Column(String(30),  default="")

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "nombre":        self.nombre        or "",
            "email":         self.email         or "",
            "phone":         self.phone         or "",
            "referral_code": self.referral_code or "",
            "created":       self.created       or "",
        }


def _winner_prize(w: dict) -> float:
    """Normalise prize amount from any winner dict regardless of tipo."""
    tipo = w.get("tipo", "bingo")
    if tipo == "linea": return float(w.get("linea_prize", 0) or 0)
    if tipo == "u":     return float(w.get("u_prize",     0) or 0)
    if tipo == "o":     return float(w.get("o_prize",     0) or 0)
    return float(w.get("prize", 0) or 0)


class Winner(Base):
    """Permanent winner record — survives session deletion."""
    __tablename__ = "winners"

    id           = Column(Integer,     primary_key=True, autoincrement=True)
    session_id   = Column(String(20),  default="", index=True)
    cartilla_id  = Column(String(10),  default="")
    tipo         = Column(String(10),  default="bingo")   # bingo | linea | u | o
    nombre       = Column(String(60),  default="")
    apellidos    = Column(String(60),  default="")
    email        = Column(String(100), default="")
    celular      = Column(String(20),  default="")
    yape_plin    = Column(String(20),  default="")
    prize_amount = Column(Float,       default=0.0)
    drawn_count  = Column(Integer,     default=0)
    claimed_at   = Column(String(30),  default="")
    game_id      = Column(String(40),  default="")
    n_winners    = Column(Integer,     default=1)
    split        = Column(Boolean,     default=False)
    merged_o     = Column(Float,       default=0.0)
    merged_u     = Column(Float,       default=0.0)
    linea_row    = Column(Integer,     nullable=True)
    puede_reclamar_bingo = Column(Boolean, default=False)
    paid         = Column(Boolean,     default=False)
    paid_at      = Column(String(30),  default="")
    paid_method  = Column(String(30),  default="")
    paid_ref     = Column(String(80),  default="")
    paid_note    = Column(String(200), default="")
    created      = Column(String(30),  default="")

    __table_args__ = (
        Index("ix_winners_session", "session_id"),
        Index("ix_winners_uniq", "session_id", "cartilla_id", "tipo", unique=True),
    )

    def to_dict(self) -> dict:
        tipo = self.tipo or "bingo"
        return {
            "session_id":   self.session_id  or "",
            "cartilla_id":  self.cartilla_id or "",
            "tipo":         tipo,
            "nombre":       self.nombre      or "",
            "apellidos":    self.apellidos   or "",
            "email":        self.email       or "",
            "celular":      self.celular     or "",
            "yape_plin":    self.yape_plin   or "",
            "prize":        self.prize_amount if tipo == "bingo" else 0,
            "linea_prize":  self.prize_amount if tipo == "linea" else 0,
            "u_prize":      self.prize_amount if tipo == "u"     else 0,
            "o_prize":      self.prize_amount if tipo == "o"     else 0,
            "prize_amount": self.prize_amount or 0,
            "drawn_count":  self.drawn_count  or 0,
            "claimed_at":   self.claimed_at   or "",
            "game_id":      self.game_id      or "",
            "n_winners":    self.n_winners    or 1,
            "split":        bool(self.split),
            "merged_o":     self.merged_o     or 0,
            "merged_u":     self.merged_u     or 0,
            "linea_row":    self.linea_row,
            "puede_reclamar_bingo": bool(self.puede_reclamar_bingo),
            "paid":         bool(self.paid),
            "paid_at":      self.paid_at      or "",
            "paid_method":  self.paid_method  or "",
            "paid_ref":     self.paid_ref      or "",
            "paid_note":    self.paid_note     or "",
            "created":      self.created       or "",
        }


class Referral(Base):
    """Tracks referral relationships and confirmation status."""
    __tablename__ = "referrals"

    id                  = Column(Integer,    primary_key=True, autoincrement=True)
    referrer_contact_id = Column(Integer,    default=0, index=True)
    referrer_code       = Column(String(12), default="")
    referrer_email      = Column(String(100),default="")
    referrer_nombre     = Column(String(120),default="")
    voucher_code        = Column(String(10), default="", index=True)
    referred_nombre     = Column(String(120),default="")
    status              = Column(String(20), default="pending")   # pending | confirmed
    confirm_token       = Column(String(64), default="", unique=True, index=True)
    created_at          = Column(String(30), default="")
    confirmed_at        = Column(String(30), default="")

    def to_dict(self) -> dict:
        return {
            "id":                  self.id,
            "referrer_contact_id": self.referrer_contact_id,
            "referrer_code":       self.referrer_code  or "",
            "referrer_email":      self.referrer_email or "",
            "referrer_nombre":     self.referrer_nombre or "",
            "voucher_code":        self.voucher_code   or "",
            "referred_nombre":     self.referred_nombre or "",
            "status":              self.status         or "pending",
            "confirm_token":       self.confirm_token  or "",
            "created_at":          self.created_at     or "",
            "confirmed_at":        self.confirmed_at   or "",
        }