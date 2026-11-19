"""API de l'interface web.

    uvicorn --factory trdr.api.main:create_app

L'API n'a pas les clés Binance : elle lit la base, et marque les signaux et les
positions que le robot traitera à son prochain passage.
"""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..db import make_session_factory, utcnow
from ..models import (
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    count_open_positions,
    get_mode,
    set_setting,
)
from .auth import AuthError, AuthService


class LoginIn(BaseModel):
    password: str
    code: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ModeIn(BaseModel):
    mode: Literal["manual", "auto"]


class StatusOut(BaseModel):
    mode: str
    symbol: str
    timeframe: str
    binance_env: str
    open_positions: int
    pending_signals: int


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    symbol: str
    timeframe: str
    candle_time: datetime
    price: float
    stop_loss: float
    take_profit: float
    rsi: float
    sma_fast: float
    sma_slow: float
    status: str
    decided_at: datetime | None
    note: str | None


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    signal_id: int | None
    symbol: str
    opened_at: datetime
    entry_price: float
    quantity: float
    cost: float
    stop_loss: float
    take_profit: float
    status: str
    close_requested: bool
    closed_at: datetime | None
    exit_price: float | None
    exit_reason: str | None
    pnl: float | None


bearer = HTTPBearer(auto_error=False)


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def require_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    if credentials is None or not request.app.state.auth.verify_token(credentials.credentials):
        raise HTTPException(401, "Authentification requise", headers={"WWW-Authenticate": "Bearer"})


def create_app(settings: Settings | None = None, session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="trdr", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.session_factory = session_factory or make_session_factory(settings.database_url)
    app.state.auth = AuthService(settings)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    protected = [Depends(require_user)]

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/auth/login", response_model=TokenOut)
    def login(body: LoginIn) -> TokenOut:
        try:
            return TokenOut(access_token=app.state.auth.login(body.password, body.code))
        except AuthError as exc:
            raise HTTPException(exc.status_code, exc.detail) from None

    @app.get("/api/status", response_model=StatusOut, dependencies=protected)
    def status(session: Session = Depends(get_session)) -> StatusOut:
        pending = session.scalars(select(Signal.id).where(Signal.status == SignalStatus.PENDING)).all()
        return StatusOut(
            mode=get_mode(session),
            symbol=settings.symbol,
            timeframe=settings.timeframe,
            binance_env=settings.binance_env,
            open_positions=count_open_positions(session),
            pending_signals=len(pending),
        )

    @app.put("/api/settings/mode", response_model=ModeIn, dependencies=protected)
    def set_mode(body: ModeIn, session: Session = Depends(get_session)) -> ModeIn:
        set_setting(session, "mode", body.mode)
        session.commit()
        return body

    @app.get("/api/signals", response_model=list[SignalOut], dependencies=protected)
    def list_signals(limit: int = 50, session: Session = Depends(get_session)):
        return session.scalars(select(Signal).order_by(Signal.id.desc()).limit(min(limit, 200))).all()

    def decide(session: Session, signal_id: int, status: str) -> Signal:
        sig = session.get(Signal, signal_id)
        if sig is None:
            raise HTTPException(404, "Signal introuvable")
        if sig.status != SignalStatus.PENDING:
            raise HTTPException(409, f"Signal déjà traité ({sig.status})")
        now = utcnow()
        if now - sig.created_at > timedelta(hours=settings.signal_ttl_hours):
            sig.status = SignalStatus.EXPIRED
            sig.decided_at = now
            session.commit()
            raise HTTPException(409, "Signal expiré")
        sig.status = status
        sig.decided_at = now
        session.commit()
        return sig

    @app.post("/api/signals/{signal_id}/approve", response_model=SignalOut, dependencies=protected)
    def approve_signal(signal_id: int, session: Session = Depends(get_session)):
        return decide(session, signal_id, SignalStatus.APPROVED)

    @app.post("/api/signals/{signal_id}/reject", response_model=SignalOut, dependencies=protected)
    def reject_signal(signal_id: int, session: Session = Depends(get_session)):
        return decide(session, signal_id, SignalStatus.REJECTED)

    @app.get("/api/positions", response_model=list[PositionOut], dependencies=protected)
    def list_positions(status: Literal["open", "closed"] | None = None, limit: int = 50, session: Session = Depends(get_session)):
        query = select(Position).order_by(Position.id.desc()).limit(min(limit, 200))
        if status:
            query = query.where(Position.status == status)
        return session.scalars(query).all()

    @app.post("/api/positions/{position_id}/close", response_model=PositionOut, dependencies=protected)
    def close_position(position_id: int, session: Session = Depends(get_session)):
        position = session.get(Position, position_id)
        if position is None:
            raise HTTPException(404, "Position introuvable")
        if position.status != PositionStatus.OPEN:
            raise HTTPException(409, "Position déjà clôturée")
        position.close_requested = True
        session.commit()
        return position

    return app
