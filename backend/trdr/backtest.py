"""Backtest de la stratégie sur un historique de bougies.

    python -m trdr.backtest --days 365        # historique public Binance
    python -m trdr.backtest --csv bougies.csv # colonnes : time,open,high,low,close,volume

Le signal est calculé à la clôture d'une bougie et l'achat se fait à l'ouverture
de la suivante. Si le stop-loss et le take-profit sont touchés dans la même
bougie, on retient le stop-loss.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import get_settings
from .exchange import candles_frame
from .strategy import StrategyParams, add_indicators, is_entry, levels, timeframe_delta


@dataclass(frozen=True)
class Trade:
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    reason: str
    return_pct: float  # frais inclus


def run_backtest(
    df: pd.DataFrame, p: StrategyParams, *, tf: timedelta, max_hold: timedelta, fee_pct: float = 0.001
) -> list[Trade]:
    """Une seule position à la fois. Une position encore ouverte à la fin est ignorée."""
    ind = add_indicators(df, p).reset_index(drop=True)
    trades: list[Trade] = []
    n = len(ind)
    i = 1
    while i < n - 1:
        if not is_entry(ind.iloc[i - 1], ind.iloc[i], p):
            i += 1
            continue

        entry = ind.iloc[i + 1]
        entry_price = float(entry.open)
        stop_loss, take_profit = levels(entry_price, p)
        closed = None
        for j in range(i + 1, n):
            row = ind.iloc[j]
            if row.low <= stop_loss:
                closed = (min(stop_loss, float(row.open)), "stop_loss")
            elif row.high >= take_profit:
                closed = (max(take_profit, float(row.open)), "take_profit")
            elif row.time + tf - entry.time >= max_hold:
                closed = (float(row.close), "time_stop")
            if closed:
                break
        if closed is None:
            break

        exit_price, reason = closed
        net = exit_price * (1 - fee_pct) / (entry_price * (1 + fee_pct)) - 1
        trades.append(Trade(entry.time, entry_price, row.time + tf, exit_price, reason, net))
        i = j + 1
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "total_return": 0.0, "avg_return": 0.0, "max_drawdown": 0.0}
    equity, peak, max_drawdown = 1.0, 1.0, 0.0
    for trade in trades:
        equity *= 1 + trade.return_pct
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
    returns = [t.return_pct for t in trades]
    return {
        "trades": len(trades),
        "win_rate": sum(r > 0 for r in returns) / len(returns),
        "total_return": equity - 1,
        "avg_return": sum(returns) / len(returns),
        "max_drawdown": max_drawdown,
    }


def fetch_history(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Bougies clôturées du marché réel (données publiques, sans clé)."""
    import ccxt

    exchange = ccxt.binance({"enableRateLimit": True})
    now = datetime.now(timezone.utc)
    since = int((now - timedelta(days=days)).timestamp() * 1000)
    rows: list[list] = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        rows += batch
        if len(batch) < 1000:
            break
        since = batch[-1][0] + 1
    df = candles_frame(rows)
    return df[df["time"] + timeframe_delta(timeframe) <= now].reset_index(drop=True)


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Backtest de la stratégie trdr")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--csv", help="fichier de bougies (time,open,high,low,close,volume)")
    source.add_argument("--days", type=int, default=365, help="jours d'historique à télécharger")
    parser.add_argument("--fee", type=float, default=0.001, help="frais par ordre (0.001 = 0,1 %%)")
    args = parser.parse_args()

    if args.csv:
        df = pd.read_csv(args.csv)
        df["time"] = pd.to_datetime(df["time"], utc=True)
    else:
        df = fetch_history(settings.symbol, settings.timeframe, args.days)

    params = StrategyParams.from_settings(settings)
    trades = run_backtest(
        df,
        params,
        tf=timeframe_delta(settings.timeframe),
        max_hold=timedelta(days=settings.max_hold_days),
        fee_pct=args.fee,
    )
    stats = summarize(trades)

    print(f"{settings.symbol} {settings.timeframe} — {len(df)} bougies, du {df['time'].iloc[0]:%Y-%m-%d} au {df['time'].iloc[-1]:%Y-%m-%d}")
    for t in trades:
        print(f"  {t.entry_time:%Y-%m-%d %H:%M} {t.entry_price:>10.2f} -> {t.exit_time:%Y-%m-%d %H:%M} {t.exit_price:>10.2f}  {t.return_pct:+7.2%}  {t.reason}")
    print(
        f"Trades : {stats['trades']} | gagnants : {stats['win_rate']:.0%} | "
        f"rendement cumulé : {stats['total_return']:+.2%} | moyen : {stats['avg_return']:+.2%} | "
        f"perte maximale : {stats['max_drawdown']:.2%}"
    )


if __name__ == "__main__":
    main()
