"""Indicateurs techniques."""

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Moyenne mobile simple."""
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI avec lissage de Wilder. Vaut 50 quand le cours ne bouge pas."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    out = 100 - 100 / (1 + avg_gain / avg_loss)
    flat = (avg_gain == 0) & (avg_loss == 0)
    return out.mask(flat, 50.0)
