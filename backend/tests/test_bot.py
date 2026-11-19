from datetime import timedelta

import pytest
from sqlalchemy import select

from trdr import bot as bot_module
from trdr.bot import LAST_CANDLE_KEY, Bot
from trdr.models import Mode, Position, PositionStatus, Signal, SignalStatus, get_setting, set_setting
from trdr.strategy import Proposal

from .conftest import LAST_CLOSED, NOW, FakeExchange, RecordingNotifier, make_candles

TF = timedelta(hours=4)


@pytest.fixture
def exchange():
    return FakeExchange()


@pytest.fixture
def notifier():
    return RecordingNotifier()


@pytest.fixture
def bot(settings, exchange, notifier, session_factory):
    return Bot(settings, exchange, notifier, session_factory)


@pytest.fixture
def buy_signal(monkeypatch):
    """Force la stratégie à proposer un achat sur la dernière bougie reçue."""
    seen = []

    def fake_evaluate(df, params):
        seen.append(df)
        return Proposal(df["time"].iloc[-1].to_pydatetime(), 100.0, 95.0, 110.0, 41.0, 105.0, 98.0)

    monkeypatch.setattr(bot_module, "evaluate", fake_evaluate)
    return seen


def one(session_factory, model):
    with session_factory() as session:
        return session.scalars(select(model)).one()


def set_mode(session_factory, mode):
    with session_factory() as session:
        set_setting(session, "mode", mode)
        session.commit()


def approve(session_factory):
    with session_factory() as session:
        session.scalars(select(Signal)).one().status = SignalStatus.APPROVED
        session.commit()


def test_manual_mode_waits_for_validation(bot, exchange, notifier, session_factory, buy_signal):
    bot.tick(NOW)
    sig = one(session_factory, Signal)
    assert sig.status == SignalStatus.PENDING
    assert sig.candle_time == LAST_CLOSED
    assert exchange.buys == []
    assert "à valider" in notifier.messages[0]

    bot.tick(NOW + timedelta(minutes=1))  # même bougie : ni nouveau signal ni nouvel appel
    assert exchange.candle_calls == 1
    assert exchange.buys == []

    approve(session_factory)
    exchange.price = 102.0
    bot.tick(NOW + timedelta(minutes=2))
    assert exchange.buys == [20.0]
    position = one(session_factory, Position)
    assert one(session_factory, Signal).status == SignalStatus.EXECUTED
    assert position.entry_price == 102.0
    assert position.stop_loss == pytest.approx(102.0 * 0.95)  # niveaux calculés sur le prix d'exécution
    assert position.take_profit == pytest.approx(102.0 * 1.10)


def test_auto_mode_buys_in_same_tick(bot, exchange, session_factory, buy_signal):
    set_mode(session_factory, Mode.AUTO)
    bot.tick(NOW)
    assert exchange.buys == [20.0]
    assert one(session_factory, Position).status == PositionStatus.OPEN


def test_in_progress_candle_is_ignored(bot, exchange, buy_signal):
    exchange.candles = make_candles(end=LAST_CLOSED + TF)  # contient la bougie de 12:00, pas encore clôturée
    bot.tick(NOW)
    assert buy_signal[0]["time"].iloc[-1] == LAST_CLOSED


def test_waits_for_candle_to_be_published(bot, exchange, session_factory, buy_signal):
    exchange.candles = make_candles(end=LAST_CLOSED - TF)
    bot.tick(NOW)
    assert buy_signal == []
    with session_factory() as session:
        assert get_setting(session, LAST_CANDLE_KEY) is None


@pytest.mark.parametrize(
    ("price", "elapsed", "reason"),
    [
        (94.0, timedelta(hours=1), "stop_loss"),
        (111.0, timedelta(hours=1), "take_profit"),
        (100.0, timedelta(days=7, minutes=1), "time_stop"),
    ],
)
def test_exits(bot, exchange, notifier, session_factory, buy_signal, monkeypatch, price, elapsed, reason):
    set_mode(session_factory, Mode.AUTO)
    bot.tick(NOW)
    monkeypatch.setattr(bot_module, "evaluate", lambda df, params: None)

    exchange.price = 100.5
    bot.tick(NOW + timedelta(minutes=1))
    assert exchange.sells == []

    exchange.price = price
    bot.tick(NOW + elapsed)
    position = one(session_factory, Position)
    assert position.status == PositionStatus.CLOSED
    assert position.exit_reason == reason
    assert position.pnl == pytest.approx(0.2 * price - 20.0)
    assert exchange.sells == [pytest.approx(0.2)]
    assert "clôturée" in notifier.messages[-1]


def test_manual_close_request(bot, exchange, session_factory, buy_signal):
    set_mode(session_factory, Mode.AUTO)
    bot.tick(NOW)
    with session_factory() as session:
        session.scalars(select(Position)).one().close_requested = True
        session.commit()
    bot.tick(NOW + timedelta(minutes=1))
    assert one(session_factory, Position).exit_reason == "manual"


def test_position_limit_blocks_new_signals(bot, exchange, session_factory, buy_signal):
    set_mode(session_factory, Mode.AUTO)
    bot.tick(NOW)
    exchange.candles = make_candles(end=LAST_CLOSED + TF)
    bot.tick(NOW + TF)
    with session_factory() as session:
        assert len(session.scalars(select(Signal)).all()) == 1
    assert exchange.buys == [20.0]


def test_pending_signal_expires(bot, exchange, session_factory, buy_signal, monkeypatch):
    bot.tick(NOW)
    monkeypatch.setattr(bot_module, "evaluate", lambda df, params: None)
    exchange.candles = make_candles(end=LAST_CLOSED + TF * 3)
    bot.tick(NOW + timedelta(hours=8, minutes=1))
    assert one(session_factory, Signal).status == SignalStatus.EXPIRED
    assert exchange.buys == []


def test_failed_buy_is_not_retried(bot, exchange, notifier, session_factory, buy_signal):
    set_mode(session_factory, Mode.AUTO)
    exchange.fail_buy = True
    bot.tick(NOW)
    exchange.fail_buy = False
    bot.tick(NOW + timedelta(minutes=1))
    sig = one(session_factory, Signal)
    assert sig.status == SignalStatus.FAILED
    assert "solde insuffisant" in sig.note
    assert exchange.buys == []


def test_failed_sell_is_retried(bot, exchange, notifier, session_factory, buy_signal):
    set_mode(session_factory, Mode.AUTO)
    bot.tick(NOW)
    exchange.price, exchange.fail_sell = 90.0, True
    bot.tick(NOW + timedelta(minutes=1))
    bot.tick(NOW + timedelta(minutes=2))
    assert one(session_factory, Position).status == PositionStatus.OPEN
    assert sum("en échec" in m for m in notifier.messages) == 1  # une seule alerte

    exchange.fail_sell = False
    bot.tick(NOW + timedelta(minutes=3))
    assert one(session_factory, Position).status == PositionStatus.CLOSED


def test_candle_outage_does_not_stop_position_monitoring(bot, exchange, session_factory, buy_signal):
    set_mode(session_factory, Mode.AUTO)
    bot.tick(NOW)
    exchange.fail_candles = True
    exchange.price = 90.0
    bot.tick(NOW + TF)
    assert one(session_factory, Position).exit_reason == "stop_loss"


def test_startup_reports_interrupted_buy(bot, notifier, session_factory, buy_signal):
    bot.tick(NOW)
    with session_factory() as session:
        session.scalars(select(Signal)).one().status = SignalStatus.EXECUTING
        session.commit()
    bot.startup_check()
    assert "interrompu" in notifier.messages[-1]
