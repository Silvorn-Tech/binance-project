# src/cli.py
import os
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger
from telegram import Bot


from hermes.providers.binance import Binance, BinanceMarketData
from hermes.providers.Telegram import TelegramNotifier
from hermes.utils.logging_config import setup_logging
from hermes.service.bot_service import BotService
from hermes.controller import Controller
from hermes.persistence.db import init_db
from hermes.service.performance_job import run_performance_window_job


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
    # DATABASE
    # =========================
    init_db()

    # =========================
    # PERFORMANCE JOB
    # =========================
    def _performance_loop():
        while True:
            try:
                run_performance_window_job(window_minutes=60)
            except Exception as e:
                logger.warning("Performance job loop failed: %s", e)
            time.sleep(60 * 60)

    threading.Thread(target=_performance_loop, daemon=True).start()

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
    market_data = BinanceMarketData()

    telegram_bot = Bot(token=telegram_token)

    notifier = TelegramNotifier(
        bot=telegram_bot,
        chat_id=int(telegram_chat_id),
    )


    # =========================
    # SERVICES
    # =========================
    bot_service = BotService(
        binance=binance,
        market_data=market_data,
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
        logger.warning("CTRL+C received. Stopping all bots...")
        bot_service.stop_all()



if __name__ == "__main__":
    main()
