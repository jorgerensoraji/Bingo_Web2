"""
models.py — Bingo Pro v8.0
SQLAlchemy ORM models replacing the JSON file system.
"""

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, Text, Index
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
    # JSON-encoded list of cartilla IDs  e.g. '["AB12", "CD34"]'
    cartillas_ids        = Column(Text,        default="[]")
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
            "pin_hint":               self.pin_hint or "",
            "cartillas":              self.cartillas_list(),
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