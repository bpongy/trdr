"""Notifications. Telegram ne sert qu'à prévenir : aucun ordre ne passe par là."""

import logging
from typing import Protocol

import httpx

log = logging.getLogger("trdr.notifier")


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class LogNotifier:
    def send(self, text: str) -> None:
        log.info("notification : %s", text.replace("\n", " | "))


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id

    def send(self, text: str) -> None:
        try:
            response = httpx.post(self._url, json={"chat_id": self._chat_id, "text": text}, timeout=10)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Ne pas journaliser l'exception telle quelle : son message contient le jeton.
            log.warning("notification Telegram non envoyée (%s)", type(exc).__name__)


def make_notifier(settings) -> Notifier:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return LogNotifier()
