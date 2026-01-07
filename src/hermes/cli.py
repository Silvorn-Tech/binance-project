# src/cli.py
import os
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

from providers.binance import Binance
from providers.Telegram import TelegramNotifier
from utils.logging_config import setup_logging
from service.bot_service import BotService
from controller import Controller


def main() -> None:
    """
    Application entrypoint.
    Responsibilities:
    - Load environment variables
    - Setup logging
    - Initialize providers
    - Initialize services
    - Start Telegram controller
    """

    # =========================
    # ENV & LOGGING
    # =========================
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=True)
    setup_logging()

    logger.info("🚀 Starting Binance Bot Microservice")

    # =========================
    # ENV VALIDATION
    # =========================
    binance_api_key = os.getenv("BINANCE_API_KEY")
    binance_api_secret = os.getenv("BINANCE_API_SECRET")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not binance_api_key or not binance_api_secret:
        raise RuntimeError("Missing BINANCE_API_KEY or BINANCE_API_SECRET")

    if not telegram_token or not telegram_chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    # =========================
    # PROVIDERS
    # =========================
    binance = Binance(
        api_key=binance_api_key,
        api_secret=binance_api_secret,
    )

    notifier = TelegramNotifier(
        bot_token=telegram_token,
        chat_id=int(telegram_chat_id),
    )


    # =========================
    # SERVICES
    # =========================
    bot_service = BotService(
        binance=binance,
        notifier=notifier,
    )

    # =========================
    # CONTROLLER
    # =========================
    controller = Controller(
        bot_service=bot_service,
        telegram_token=telegram_token,
    )

    logger.info("📡 Telegram controller initialized")

    # =========================
    # START
    # =========================
    try:
        controller.start()
    except KeyboardInterrupt:
        logger.warning("🛑 KeyboardInterrupt received, shutting down...")
        bot_service.stop_all()
        logger.info("👋 Service stopped cleanly")


if __name__ == "__main__":
    main()
