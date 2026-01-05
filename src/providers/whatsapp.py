from dataclasses import dataclass
from loguru import logger
from twilio.rest import Client

@dataclass
class WhatsAppNotifier:
    account_sid: str
    auth_token: str
    from_whatsapp: str  # e.g. "whatsapp:+14155238886"
    to_whatsapp: str    # e.g. "whatsapp:+57XXXXXXXXXX"

    def __post_init__(self) -> None:
        self._client = Client(self.account_sid, self.auth_token)

    def send(self, text: str) -> None:
        try:
            self._client.messages.create(
                from_=self.from_whatsapp,
                to=self.to_whatsapp,
                body=text,
            )
            logger.info("WHATSAPP | sent")
        except Exception as e:
            # Importante: nunca tumbar el bot por una notificación
            logger.warning(f"WHATSAPP | failed: {e}")
