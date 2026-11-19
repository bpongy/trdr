from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trdr.backtest import Trade, run_backtest, summarize
from trdr.indicators import rsi, sma
from trdr.strategy import StrategyParams, add_indicators, evaluate, is_entry, last_closed_candle_open, timeframe_delta

TF = timedelta(hours=4)
PARAMS = StrategyParams()


def candles(closes: list[float]) -> pd.DataFrame:
    """Bougies dont l'ouverture est la clôture précédente."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame(
        {
            "time": pd.to_datetime([start + TF * i for i in range(len(closes))], utc=True),
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": 1.0,
        }
    )


def uptrend_with_dip(after_dip: float) -> tuple[list[float], int]:
    """Longue hausse, repli de 6 bougies, puis variation de `after_dip` par bougie."""
    closes = [100.0]
    for i in range(1, 300):
        closes.append(closes[-1] + (0.9 if i % 2 else -0.2))
    for _ in range(6):
        closes.append(closes[-1] * 0.985)
    dip_end = len(closes) - 1
    for _ in range(30):
        closes.append(closes[-1] * (1 + after_dip))
    return closes, dip_end


def entries(df: pd.DataFrame) -> list[int]:
    ind = add_indicators(df, PARAMS)
    return [i for i in range(1, len(ind)) if is_entry(ind.iloc[i - 1], ind.iloc[i], PARAMS)]


def test_sma():
    assert sma(pd.Series([1.0, 2.0, 3.0, 4.0]), 2).tolist()[1:] == [1.5, 2.5, 3.5]


def test_rsi_bounds():
    assert rsi(pd.Series(range(1, 60), dtype=float)).iloc[-1] == 100
    assert rsi(pd.Series(range(60, 1, -1), dtype=float)).iloc[-1] == 0
    assert rsi(pd.Series([5.0] * 60)).iloc[-1] == 50


def test_timeframe_helpers():
    assert timeframe_delta("4h") == timedelta(hours=4)
    assert timeframe_delta("1d") == timedelta(days=1)
    with pytest.raises(ValueError):
        timeframe_delta("1w")
    now = datetime(2026, 1, 10, 12, 30, tzinfo=timezone.utc)
    assert last_closed_candle_open(now, TF) == datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)


def test_entry_after_dip_in_uptrend():
    closes, dip_end = uptrend_with_dip(after_dip=0.01)
    found = entries(candles(closes))
    assert len(found) == 1
    assert dip_end < found[0] <= dip_end + 5


def test_no_entry_without_dip_or_in_downtrend():
    assert entries(candles([100 + i * 0.5 for i in range(400)])) == []
    assert entries(candles([400 - i * 0.5 for i in range(400)])) == []


def test_evaluate_returns_levels_on_last_candle():
    closes, _ = uptrend_with_dip(after_dip=0.01)
    df = candles(closes)
    index = entries(df)[0]

    assert evaluate(df.iloc[:index], PARAMS) is None
    proposal = evaluate(df.iloc[: index + 1], PARAMS)
    assert proposal.candle_time == df["time"].iloc[index].to_pydatetime()
    assert proposal.price == closes[index]
    assert proposal.stop_loss == pytest.approx(closes[index] * 0.95)
    assert proposal.take_profit == pytest.approx(closes[index] * 1.10)


def test_backtest_take_profit():
    closes, _ = uptrend_with_dip(after_dip=0.01)
    df = candles(closes)
    index = entries(df)[0]

    trades = run_backtest(df, PARAMS, tf=TF, max_hold=timedelta(days=7), fee_pct=0.001)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_time == df["time"].iloc[index + 1]  # achat à l'ouverture de la bougie suivante
    assert trade.entry_price == df["open"].iloc[index + 1]
    assert trade.reason == "take_profit"
    assert trade.exit_price == pytest.approx(trade.entry_price * 1.10)
    assert trade.return_pct == pytest.approx(1.10 * 0.999 / 1.001 - 1)


def test_backtest_stop_loss_and_time_stop():
    closes, _ = uptrend_with_dip(after_dip=0.01)
    index = entries(candles(closes))[0]

    crash = closes[: index + 2] + [closes[index + 1] * 0.98**k for k in range(1, 10)]
    trade = run_backtest(candles(crash), PARAMS, tf=TF, max_hold=timedelta(days=7))[0]
    assert trade.reason == "stop_loss"
    assert trade.exit_price == pytest.approx(trade.entry_price * 0.95)

    flat = closes[: index + 2] + [closes[index + 1]] * 60
    trade = run_backtest(candles(flat), PARAMS, tf=TF, max_hold=timedelta(days=7))[0]
    assert trade.reason == "time_stop"
    assert trade.exit_time - trade.entry_time == timedelta(days=7)


def test_summarize():
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    trades = [Trade(t0, 100, t0, 110, "take_profit", 0.10), Trade(t0, 100, t0, 95, "stop_loss", -0.05)]
    stats = summarize(trades)
    assert stats["trades"] == 2
    assert stats["win_rate"] == 0.5
    assert stats["total_return"] == pytest.approx(1.10 * 0.95 - 1)
    assert stats["max_drawdown"] == pytest.approx(0.05)
    assert summarize([])["trades"] == 0
