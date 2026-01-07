from dataclasses import dataclass
from loguru import logger
import requests


@dataclass
class TelegramNotifier:
    bot_token: str
    chat_id: int

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }

        try:
            r = requests.post(url, json=payload, timeout=5)
            r.raise_for_status()
            logger.info("TELEGRAM | message sent")
        except Exception as e:
            logger.warning(f"TELEGRAM | failed: {e}")
