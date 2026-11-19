"""Tables : signaux, positions et réglages."""

from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, UTCDateTime, utcnow


class SignalStatus:
    PENDING = "pending"  # en attente de validation (mode manuel)
    APPROVED = "approved"  # validé, le robot passera l'ordre
    EXECUTING = "executing"  # ordre en cours d'envoi
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"

    ACTIVE = (PENDING, APPROVED, EXECUTING)


class PositionStatus:
    OPEN = "open"
    CLOSED = "closed"


class Mode:
    MANUAL = "manual"
    AUTO = "auto"


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "candle_time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    symbol: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(8))
    candle_time: Mapped[datetime] = mapped_column(UTCDateTime)
    price: Mapped[float]
    stop_loss: Mapped[float]
    take_profit: Mapped[float]
    rsi: Mapped[float]
    sma_fast: Mapped[float]
    sma_slow: Mapped[float]
    status: Mapped[str] = mapped_column(String(12), default=SignalStatus.PENDING, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    note: Mapped[str | None] = mapped_column(String(500), default=None)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), default=None)
    symbol: Mapped[str] = mapped_column(String(20))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    entry_price: Mapped[float]
    quantity: Mapped[float]
    cost: Mapped[float]
    stop_loss: Mapped[float]
    take_profit: Mapped[float]
    status: Mapped[str] = mapped_column(String(8), default=PositionStatus.OPEN, index=True)
    close_requested: Mapped[bool] = mapped_column(default=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    exit_price: Mapped[float | None] = mapped_column(default=None)
    exit_reason: Mapped[str | None] = mapped_column(String(16), default=None)
    pnl: Mapped[float | None] = mapped_column(default=None)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


def get_mode(session: Session) -> str:
    """Mode manuel par défaut : rien n'est acheté sans validation."""
    return get_setting(session, "mode", Mode.MANUAL)


def count_open_positions(session: Session) -> int:
    return len(session.scalars(select(Position.id).where(Position.status == PositionStatus.OPEN)).all())


def count_active_signals(session: Session) -> int:
    return len(session.scalars(select(Signal.id).where(Signal.status.in_(SignalStatus.ACTIVE))).all())
