from dataclasses import dataclass
from loguru import logger
from datetime import date
import requests


@dataclass
class TelegramNotifier:
    bot_token: str
    chat_id: int
    max_daily_messages: int = 20

    def __post_init__(self) -> None:
        self._sent_today = 0
        self._current_day = date.today()

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._current_day:
            self._current_day = today
            self._sent_today = 0

    def send(self, text: str) -> None:
        self._reset_if_new_day()

        if self._sent_today >= self.max_daily_messages:
            logger.warning("TELEGRAM | local daily limit reached, skipping send")
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }

        try:
            r = requests.post(url, json=payload, timeout=5)
            r.raise_for_status()
            self._sent_today += 1
            logger.info(f"TELEGRAM | sent ({self._sent_today}/{self.max_daily_messages})")
        except Exception as e:
            logger.warning(f"TELEGRAM | failed: {e}")
