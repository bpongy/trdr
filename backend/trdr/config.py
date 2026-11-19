"""Configuration, lue depuis les variables d'environnement (ou un fichier .env)."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./trdr.db"

    # Marché
    symbol: str = "ETH/USDT"
    timeframe: str = "4h"

    # Stratégie : tendance par moyennes mobiles, entrée sur repli du RSI
    sma_fast: int = 50
    sma_slow: int = 200
    rsi_period: int = 14
    rsi_entry: float = 40.0

    # Risque
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    max_hold_days: float = 7.0
    trade_size_usdt: float = 20.0
    max_open_positions: int = 1
    signal_ttl_hours: float = 8.0

    # Boucle du robot
    tick_seconds: int = 60

    # Binance — uniquement des environnements virtuels, jamais le compte réel
    binance_env: Literal["testnet", "demo"] = "testnet"
    binance_api_key: str = ""
    binance_api_secret: str = ""
    # "sandbox" : bougies de l'environnement virtuel ; "live" : bougies du marché
    # réel, en lecture seule et sans clé (les ordres restent sur le compte virtuel).
    market_data_source: Literal["sandbox", "live"] = "sandbox"

    # Notifications Telegram (sans jeton : simple écriture dans les logs)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Authentification de l'interface (voir `python -m trdr.cli init-auth`)
    auth_password_hash: str = ""
    auth_totp_secret: str = ""
    jwt_secret: str = ""
    jwt_ttl_minutes: int = 720
    cors_origins: str = ""

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if self.sma_fast >= self.sma_slow:
            raise ValueError("SMA_FAST doit être inférieur à SMA_SLOW")
        for name in ("stop_loss_pct", "take_profit_pct"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name.upper()} doit être compris entre 0 et 1 (0.05 = 5 %)")
        if self.trade_size_usdt <= 0 or self.max_open_positions < 1:
            raise ValueError("TRADE_SIZE_USDT et MAX_OPEN_POSITIONS doivent être positifs")
        return self

    @property
    def auth_configured(self) -> bool:
        return bool(self.auth_password_hash and self.auth_totp_secret and self.jwt_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
