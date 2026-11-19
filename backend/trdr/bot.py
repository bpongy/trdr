"""Boucle du robot.

À chaque passage : expire les signaux trop vieux, cherche un signal sur la dernière
bougie clôturée, exécute les signaux validés, puis surveille les positions ouvertes.
Le robot est le seul processus qui parle à Binance : l'API ne fait que marquer
les signaux et les positions, il s'en charge au passage suivant.
"""

import logging
import signal as os_signal
import sys
import threading
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .db import make_session_factory, utcnow
from .exchange import BinanceSandbox, Exchange
from .models import (
    Mode,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    count_active_signals,
    count_open_positions,
    get_mode,
    get_setting,
    set_setting,
)
from .notifier import Notifier, make_notifier
from .strategy import StrategyParams, evaluate, last_closed_candle_open, levels, timeframe_delta

log = logging.getLogger("trdr.bot")

LAST_CANDLE_KEY = "last_evaluated_candle"

EXIT_LABELS = {
    "stop_loss": "stop-loss atteint",
    "take_profit": "take-profit atteint",
    "time_stop": "durée maximale atteinte",
    "manual": "clôture demandée",
}


def exit_reason(position: Position, price: float, now: datetime, max_hold: timedelta) -> str | None:
    if position.close_requested:
        return "manual"
    if price <= position.stop_loss:
        return "stop_loss"
    if price >= position.take_profit:
        return "take_profit"
    if now - position.opened_at >= max_hold:
        return "time_stop"
    return None


class Bot:
    def __init__(self, settings: Settings, exchange: Exchange, notifier: Notifier, session_factory: sessionmaker[Session]):
        self.settings = settings
        self.exchange = exchange
        self.notifier = notifier
        self.session_factory = session_factory
        self.params = StrategyParams.from_settings(settings)
        self.tf = timeframe_delta(settings.timeframe)
        self._sell_errors: set[int] = set()

    # --- boucle -----------------------------------------------------------------

    def run_forever(self, stop: threading.Event) -> None:
        self.startup_check()
        log.info("robot démarré : %s %s, environnement %s", self.settings.symbol, self.settings.timeframe, self.settings.binance_env)
        while not stop.is_set():
            self.tick()
            stop.wait(self.settings.tick_seconds)
        log.info("robot arrêté")

    def startup_check(self) -> None:
        """Un signal resté en cours d'exécution = arrêt brutal pendant un achat."""
        with self.session_factory() as session:
            for sig in session.scalars(select(Signal).where(Signal.status == SignalStatus.EXECUTING)):
                text = f"Signal #{sig.id} interrompu pendant l'achat : vérifier le compte Binance avant de continuer."
                log.warning(text)
                self.notifier.send(text)

    def tick(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        steps = (self._expire_signals, self._evaluate, self._execute_approved, self._manage_positions)
        with self.session_factory() as session:
            for step in steps:
                # Une étape en échec (réseau, Binance) ne doit pas bloquer les suivantes,
                # en particulier la surveillance des positions.
                try:
                    step(session, now)
                except Exception:
                    session.rollback()
                    log.exception("étape %s en échec", step.__name__)

    # --- étapes -----------------------------------------------------------------

    def _expire_signals(self, session: Session, now: datetime) -> None:
        limit = now - timedelta(hours=self.settings.signal_ttl_hours)
        stale = session.scalars(select(Signal).where(Signal.status == SignalStatus.PENDING, Signal.created_at < limit)).all()
        for sig in stale:
            sig.status = SignalStatus.EXPIRED
            sig.decided_at = now
        session.commit()

    def _evaluate(self, session: Session, now: datetime) -> None:
        expected = last_closed_candle_open(now, self.tf)
        if get_setting(session, LAST_CANDLE_KEY) == expected.isoformat():
            return

        candles = self.exchange.fetch_candles(self.settings.symbol, self.settings.timeframe, self.params.warmup)
        candles = candles[candles["time"] <= expected]  # écarte la bougie en cours
        if candles.empty or candles["time"].iloc[-1] != expected:
            return  # bougie pas encore publiée : nouvel essai au prochain passage
        if len(candles) < self.params.sma_slow + 1:
            log.warning("historique insuffisant : %d bougies pour une moyenne sur %d", len(candles), self.params.sma_slow)

        proposal = evaluate(candles, self.params)
        set_setting(session, LAST_CANDLE_KEY, expected.isoformat())
        if proposal is None:
            session.commit()
            return
        if count_open_positions(session) + count_active_signals(session) >= self.settings.max_open_positions:
            log.info("signal ignoré : limite de positions atteinte")
            session.commit()
            return

        auto = get_mode(session) == Mode.AUTO
        sig = Signal(
            created_at=now,
            symbol=self.settings.symbol,
            timeframe=self.settings.timeframe,
            candle_time=proposal.candle_time,
            price=proposal.price,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            rsi=proposal.rsi,
            sma_fast=proposal.sma_fast,
            sma_slow=proposal.sma_slow,
            status=SignalStatus.APPROVED if auto else SignalStatus.PENDING,
            decided_at=now if auto else None,
        )
        session.add(sig)
        session.commit()
        self.notifier.send(
            f"Signal d'achat {sig.symbol} ({sig.timeframe})\n"
            f"Prix : {sig.price:.2f}\n"
            f"Stop-loss : {sig.stop_loss:.2f} | Take-profit : {sig.take_profit:.2f}\n"
            f"RSI : {sig.rsi:.1f}\n"
            + ("Mode automatique : ordre en cours." if auto else "Mode manuel : à valider dans l'interface.")
        )

    def _execute_approved(self, session: Session, now: datetime) -> None:
        approved = session.scalars(select(Signal).where(Signal.status == SignalStatus.APPROVED).order_by(Signal.id)).all()
        for sig in approved:
            if count_open_positions(session) >= self.settings.max_open_positions:
                sig.status = SignalStatus.FAILED
                sig.note = "limite de positions atteinte"
                session.commit()
                continue

            # Enregistré avant l'ordre : en cas d'arrêt brutal, le signal n'est pas rejoué.
            sig.status = SignalStatus.EXECUTING
            session.commit()
            try:
                fill = self.exchange.market_buy(sig.symbol, self.settings.trade_size_usdt)
            except Exception as exc:
                log.exception("achat en échec pour le signal %s", sig.id)
                sig.status = SignalStatus.FAILED
                sig.note = str(exc)[:500]
                session.commit()
                self.notifier.send(f"Achat {sig.symbol} en échec (signal #{sig.id}).")
                continue

            stop_loss, take_profit = levels(fill.price, self.params)
            position = Position(
                signal_id=sig.id,
                symbol=sig.symbol,
                opened_at=now,
                entry_price=fill.price,
                quantity=fill.quantity,
                cost=fill.cost,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            sig.status = SignalStatus.EXECUTED
            session.add(position)
            session.commit()
            self.notifier.send(
                f"Achat exécuté : {fill.quantity:.5f} {sig.symbol} à {fill.price:.2f} ({fill.cost:.2f} USDT)\n"
                f"Stop-loss : {stop_loss:.2f} | Take-profit : {take_profit:.2f}"
            )

    def _manage_positions(self, session: Session, now: datetime) -> None:
        positions = session.scalars(select(Position).where(Position.status == PositionStatus.OPEN)).all()
        if not positions:
            return
        price = self.exchange.fetch_price(self.settings.symbol)
        max_hold = timedelta(days=self.settings.max_hold_days)
        for position in positions:
            reason = exit_reason(position, price, now, max_hold)
            if reason is None:
                continue
            try:
                fill = self.exchange.market_sell(position.symbol, position.quantity)
            except Exception:
                log.exception("vente en échec pour la position %s", position.id)
                if position.id not in self._sell_errors:  # un seul message, puis nouvel essai à chaque passage
                    self._sell_errors.add(position.id)
                    self.notifier.send(f"Vente {position.symbol} en échec (position #{position.id}), nouvel essai en cours.")
                continue

            self._sell_errors.discard(position.id)
            position.status = PositionStatus.CLOSED
            position.closed_at = now
            position.exit_price = fill.price
            position.exit_reason = reason
            position.pnl = fill.cost - position.cost
            session.commit()
            self.notifier.send(
                f"Position #{position.id} clôturée ({EXIT_LABELS[reason]})\n"
                f"Vente à {fill.price:.2f} | Résultat : {position.pnl:+.2f} USDT"
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if not (settings.binance_api_key and settings.binance_api_secret):
        sys.exit("BINANCE_API_KEY et BINANCE_API_SECRET (compte virtuel) sont requis.")

    exchange = BinanceSandbox(
        settings.binance_api_key, settings.binance_api_secret, settings.binance_env, settings.market_data_source
    )
    bot = Bot(settings, exchange, make_notifier(settings), make_session_factory(settings.database_url))

    stop = threading.Event()
    for signum in (os_signal.SIGINT, os_signal.SIGTERM):
        os_signal.signal(signum, lambda *_: stop.set())
    bot.run_forever(stop)


if __name__ == "__main__":
    main()
