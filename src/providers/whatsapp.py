from dataclasses import dataclass
from loguru import logger
from twilio.rest import Client
from datetime import date

@dataclass
class WhatsAppNotifier:
    account_sid: str
    auth_token: str
    from_whatsapp: str  # e.g. "whatsapp:+14155238886"
    to_whatsapp: str    # e.g. "whatsapp:+57XXXXXXXXXX"
    max_daily_messages: int = 20

    def __post_init__(self) -> None:
        self._client = Client(self.account_sid, self.auth_token)
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
            logger.warning("WHATSAPP | local daily limit reached, skipping send")
            return

        try:
            self._client.messages.create(
                from_=self.from_whatsapp,
                to=self.to_whatsapp,
                body=text,
            )
            self._sent_today += 1
            logger.info(f"WHATSAPP | sent ({self._sent_today}/{self.max_daily_messages})")
        except Exception as e:
            logger.warning(f"WHATSAPP | failed: {e}")
