"""Accès à Binance, limité aux environnements virtuels (testnet ou demo trading)."""

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

CANDLE_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class Fill:
    """Résultat d'un ordre au marché, frais déduits."""

    quantity: float  # quantité d'actif réellement reçue (achat) ou vendue (vente)
    price: float  # prix moyen d'exécution
    cost: float  # montant payé (achat) ou encaissé net (vente), en devise de cotation


class Exchange(Protocol):
    def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame: ...

    def fetch_price(self, symbol: str) -> float: ...

    def market_buy(self, symbol: str, quote_amount: float) -> Fill: ...

    def market_sell(self, symbol: str, quantity: float) -> Fill: ...


def candles_frame(raw: list[list]) -> pd.DataFrame:
    """Bougies ccxt -> DataFrame. `time` est l'heure d'ouverture, en UTC."""
    df = pd.DataFrame(raw, columns=CANDLE_COLUMNS)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df


def _fees_in(order: dict, currency: str) -> float:
    fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
    return sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == currency)


class BinanceSandbox:
    """Client Binance spot qui ne sait passer des ordres que sur un compte virtuel."""

    def __init__(self, api_key: str, api_secret: str, env: str = "testnet", market_data_source: str = "sandbox"):
        import ccxt

        self._trade = ccxt.binance(
            {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True, "options": {"defaultType": "spot"}}
        )
        if env == "testnet":
            self._trade.set_sandbox_mode(True)
        elif env == "demo":
            self._trade.enable_demo_trading(True)
        else:
            raise ValueError(f"environnement Binance inconnu : {env!r}")

        url = self._trade.urls["api"]["private"]
        if "testnet" not in url and "demo" not in url:
            raise RuntimeError(f"refus de démarrer : {url} n'est pas un environnement virtuel")

        # Client sans clé : ne peut que lire les cours publics.
        self._data = ccxt.binance({"enableRateLimit": True}) if market_data_source == "live" else self._trade

    def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return candles_frame(self._data.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000)))

    def fetch_price(self, symbol: str) -> float:
        # Le prix suivi pour les sorties est celui du compte où la position existe.
        return float(self._trade.fetch_ticker(symbol)["last"])

    def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        order = self._trade.create_market_buy_order_with_cost(symbol, quote_amount)
        filled = float(order.get("filled") or 0)
        if filled <= 0:
            raise RuntimeError(f"ordre d'achat non exécuté : {order.get('status')}")
        base = symbol.split("/")[0]
        cost = float(order["cost"])
        # Les frais d'un achat sont prélevés sur l'actif reçu : on ne détient pas `filled`.
        return Fill(quantity=filled - _fees_in(order, base), price=float(order.get("average") or cost / filled), cost=cost)

    def market_sell(self, symbol: str, quantity: float) -> Fill:
        self._trade.load_markets()
        amount = float(self._trade.amount_to_precision(symbol, quantity))
        order = self._trade.create_market_sell_order(symbol, amount)
        filled = float(order.get("filled") or 0)
        if filled <= 0:
            raise RuntimeError(f"ordre de vente non exécuté : {order.get('status')}")
        quote = symbol.split("/")[1]
        cost = float(order["cost"])
        return Fill(quantity=filled, price=float(order.get("average") or cost / filled), cost=cost - _fees_in(order, quote))
