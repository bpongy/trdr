from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trdr.config import Settings
from trdr.db import make_session_factory
from trdr.exchange import Fill

NOW = datetime(2026, 1, 10, 12, 30, tzinfo=timezone.utc)  # bougie 4h en cours : 12:00, dernière clôturée : 08:00
LAST_CLOSED = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)


class FakeExchange:
    def __init__(self):
        self.candles = make_candles(end=LAST_CLOSED)
        self.price = 100.0
        self.buys: list[float] = []
        self.sells: list[float] = []
        self.candle_calls = 0
        self.fail_candles = self.fail_buy = self.fail_sell = False

    def fetch_candles(self, symbol, timeframe, limit):
        self.candle_calls += 1
        if self.fail_candles:
            raise ConnectionError("réseau")
        return self.candles.copy()

    def fetch_price(self, symbol):
        return self.price

    def market_buy(self, symbol, quote_amount):
        if self.fail_buy:
            raise RuntimeError("solde insuffisant")
        self.buys.append(quote_amount)
        return Fill(quantity=quote_amount / self.price, price=self.price, cost=quote_amount)

    def market_sell(self, symbol, quantity):
        if self.fail_sell:
            raise RuntimeError("refusé")
        self.sells.append(quantity)
        return Fill(quantity=quantity, price=self.price, cost=quantity * self.price)


class RecordingNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, text):
        self.messages.append(text)


def make_candles(end: datetime, n: int = 20, tf: timedelta = timedelta(hours=4), price: float = 100.0) -> pd.DataFrame:
    """`n` bougies plates dont la dernière s'ouvre à `end`."""
    times = [end - tf * (n - 1 - i) for i in range(n)]
    return pd.DataFrame(
        {"time": pd.to_datetime(times, utc=True), "open": price, "high": price, "low": price, "close": price, "volume": 1.0}
    )


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        sma_fast=3,
        sma_slow=5,
        trade_size_usdt=20.0,
    )


@pytest.fixture
def session_factory(settings):
    return make_session_factory(settings.database_url)
