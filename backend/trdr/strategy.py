"""Stratégie : acheter les replis dans une tendance haussière.

- Tendance : moyenne mobile rapide au-dessus de la lente, et cours au-dessus de la lente.
- Entrée : le RSI repasse au-dessus du seuil après être descendu en dessous.

Les fonctions ne reçoivent que des bougies clôturées.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from .indicators import rsi, sma

_UNITS = {"m": 60, "h": 3600, "d": 86400}


def timeframe_delta(timeframe: str) -> timedelta:
    """'4h' -> 4 heures. Minutes, heures et jours uniquement."""
    try:
        return timedelta(seconds=int(timeframe[:-1]) * _UNITS[timeframe[-1]])
    except (KeyError, ValueError):
        raise ValueError(f"unité de temps non gérée : {timeframe!r} (ex. 15m, 4h, 1d)") from None


def last_closed_candle_open(now: datetime, tf: timedelta) -> datetime:
    """Heure d'ouverture de la dernière bougie clôturée à l'instant `now`."""
    step = int(tf.total_seconds())
    current_open = int(now.timestamp()) // step * step
    return datetime.fromtimestamp(current_open - step, tz=timezone.utc)


@dataclass(frozen=True)
class StrategyParams:
    sma_fast: int = 50
    sma_slow: int = 200
    rsi_period: int = 14
    rsi_entry: float = 40.0
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10

    @classmethod
    def from_settings(cls, settings) -> "StrategyParams":
        return cls(
            sma_fast=settings.sma_fast,
            sma_slow=settings.sma_slow,
            rsi_period=settings.rsi_period,
            rsi_entry=settings.rsi_entry,
            stop_loss_pct=settings.stop_loss_pct,
            take_profit_pct=settings.take_profit_pct,
        )

    @property
    def warmup(self) -> int:
        """Nombre de bougies à charger pour que les indicateurs soient stables."""
        return self.sma_slow + 150


@dataclass(frozen=True)
class Proposal:
    candle_time: datetime
    price: float
    stop_loss: float
    take_profit: float
    rsi: float
    sma_fast: float
    sma_slow: float


def levels(price: float, p: StrategyParams) -> tuple[float, float]:
    """(stop-loss, take-profit) pour un prix d'entrée."""
    return price * (1 - p.stop_loss_pct), price * (1 + p.take_profit_pct)


def add_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    out["sma_fast"] = sma(out["close"], p.sma_fast)
    out["sma_slow"] = sma(out["close"], p.sma_slow)
    out["rsi"] = rsi(out["close"], p.rsi_period)
    return out


def is_entry(prev, cur, p: StrategyParams) -> bool:
    """Signal d'achat sur la bougie `cur`, `prev` étant la bougie précédente."""
    if any(pd.isna(v) for v in (cur.sma_fast, cur.sma_slow, cur.rsi, prev.rsi)):
        return False
    uptrend = cur.sma_fast > cur.sma_slow and cur.close > cur.sma_slow
    rsi_recovers = prev.rsi < p.rsi_entry <= cur.rsi
    return bool(uptrend and rsi_recovers)


def evaluate(df: pd.DataFrame, p: StrategyParams) -> Proposal | None:
    """Signal d'achat sur la dernière bougie clôturée de `df`, s'il y en a un."""
    if len(df) < 2:
        return None
    ind = add_indicators(df, p)
    prev, cur = ind.iloc[-2], ind.iloc[-1]
    if not is_entry(prev, cur, p):
        return None
    price = float(cur.close)
    stop_loss, take_profit = levels(price, p)
    return Proposal(
        candle_time=pd.Timestamp(cur.time).to_pydatetime(),
        price=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        rsi=float(cur.rsi),
        sma_fast=float(cur.sma_fast),
        sma_slow=float(cur.sma_slow),
    )
