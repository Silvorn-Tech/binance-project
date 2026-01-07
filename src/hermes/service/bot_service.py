# src/service/bot_service.py
from loguru import logger

from service.bot_builder import BotBuilder
from utils.bot import Bot
from providers.binance import Binance
from providers.Telegram import TelegramNotifier
from utils.bot_config import BotConfig


class BotService:
    """
    Application service responsible for managing bot lifecycles.
    """

    def __init__(
        self,
        binance: Binance,
        notifier: TelegramNotifier,
    ):
        self.binance = binance
        self.notifier = notifier

        # Active bots registry: symbol -> Bot
        self._bots: dict[str, Bot] = {}

        logger.info("🧠 BotService initialized")

    # =========================
    # Public API
    # =========================
    def start_bot_from_config(self, config: BotConfig):
        symbol = config.symbol

        if symbol in self._bots:
            raise RuntimeError(f"Bot already running for {symbol}")

        bot = Bot(
            config=config,
            binance=self.binance,
            notifier=self.notifier,
        )

        bot.start()
        self._bots[symbol] = bot

    def stop_bot(self, symbol: str) -> None:
        symbol = symbol.upper()

        bot = self._bots.get(symbol)
        if not bot:
            raise RuntimeError(f"No running bot for {symbol}")

        logger.warning(f"🛑 Stopping bot | symbol={symbol}")

        bot.stop()
        bot.join(timeout=10)

        del self._bots[symbol]

        self.notifier.send(
            f"🛑 BOT STOPPED\n"
            f"Symbol: {symbol}"
        )

    def restart_bot(self, symbol: str, base_asset: str, profile: str) -> None:
        symbol = symbol.upper()

        logger.info(
            f"♻️ Restarting bot | symbol={symbol} | profile={profile}"
        )

        if symbol in self._bots:
            self.stop_bot(symbol)

        self.start_bot(symbol, base_asset, profile)

    def list_bots(self) -> list[str]:
        return list(self._bots.keys())

    def stop_all(self) -> None:
        logger.warning("🛑 Stopping ALL bots")

        for symbol in list(self._bots.keys()):
            self.stop_bot(symbol)

