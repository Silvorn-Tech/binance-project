import time
from loguru import logger
from datetime import datetime
from pathlib import Path
import csv

from hermes.service.bot_builder import BotBuilder
from hermes.service.bot_state import BotRuntimeState
from hermes.utils.bot import Bot
from hermes.providers.binance import Binance
from hermes.providers.market_data import MarketData
from hermes.providers.Telegram import TelegramNotifier
from hermes.utils.bot_config import BotConfig
from hermes.utils.trading_mode import TradingMode


class BotService:
    """
    Application service responsible for managing bot lifecycles.
    """

    def __init__(self, binance: Binance, market_data: MarketData, notifier: TelegramNotifier):
        self._bots: dict[str, Bot] = {}
        self._states: dict[str, BotRuntimeState] = {}

        self.binance = binance
        self.market_data = market_data
        self.notifier = notifier

        logger.info("🧠 BotService initialized")


    # =========================
    # BOT LIFECYCLE
    # =========================
    def start_bot_from_config(self, config: BotConfig) -> None:
        symbol = config.symbol.upper()

        if symbol in self._bots:
            raise RuntimeError(f"Bot already running for {symbol}")

        logger.info(
            "🚀 Starting bot | symbol=%s | profile=%s",
            symbol,
            config.profile,
        )

        initial_mode = (
            TradingMode.SIMULATION
            if config.profile == "vortex"
            else TradingMode.LIVE
        )

        state = BotRuntimeState(
            symbol=config.symbol,
            profile=config.profile,
            base_asset=config.base_asset,
            trailing_pct=config.trailing_pct,
            config=config,
            trading_mode=initial_mode,
        )

        if state.trading_mode == TradingMode.LIVE:
            state.live_authorized = True
            state.live_authorized_at = time.time()
            state.waiting_for_confirmation = False
            state.armed = True
            state.waiting_for_signal = True


        self._states[symbol] = state

        live_binance = self.binance if state.trading_mode == TradingMode.LIVE else None
        bot = Bot(
            config=config,
            market_data=self.market_data,
            binance=live_binance,
            state=state,
            notifier=self.notifier,
        )

        bot.start()
        self._bots[symbol] = bot

    def stop_bot(self, symbol: str) -> None:
        symbol = symbol.upper()
        bot = self._bots.get(symbol)

        if not bot:
            raise RuntimeError(f"No running bot for {symbol}")

        logger.warning("🛑 Stopping bot | symbol=%s", symbol)

        bot.stop()
        bot.join(timeout=10)

        del self._bots[symbol]
        self._states.pop(symbol, None)


    def restart_bot(self, symbol: str, base_asset: str, profile: str) -> None:
        symbol = symbol.upper()

        logger.info(
            "♻️ Restarting bot | symbol=%s | profile=%s",
            symbol,
            profile,
        )

        if symbol in self._bots:
            self.stop_bot(symbol)

        config = (
            BotBuilder()
            .with_symbol(symbol, base_asset)
            .with_profile(profile)
            .with_defaults()
            .build()
        )

        self.start_bot_from_config(config)

    def enable_live(self, symbol: str) -> None:
        bot = self._bots.get(symbol.upper())
        if not bot:
            raise RuntimeError(f"No running bot for {symbol}")
        bot.binance = self.binance

    def disable_live(self, symbol: str) -> None:
        bot = self._bots.get(symbol.upper())
        if not bot:
            return
        bot.binance = None

    def stop_all(self) -> None:
        logger.warning("🛑 Stopping ALL bots")

        for symbol in list(self._bots.keys()):
            self.stop_bot(symbol)

    # =========================
    # QUERIES
    # =========================
    def list_bots(self) -> list[str]:
        return list(self._bots.keys())

    def get_bot_state(self, symbol: str) -> BotRuntimeState | None:
        return self._states.get(symbol.upper())

    def get_all_states(self) -> list[BotRuntimeState]:
        return list(self._states.values())

    def get_notifier(self, symbol: str) -> TelegramNotifier:
        # All bots share the same notifier (for now)
        return self.notifier

    def get_any_notifier(self) -> TelegramNotifier:
        return self.notifier

    # =========================
    # REPORTS
    # =========================
    def generate_global_report_csv(self) -> str | None:
        if not self._states:
            return None

        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)

        date = datetime.now().strftime("%Y-%m-%d")
        file_path = reports_dir / f"GLOBAL_{date}.csv"

        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)

            writer.writerow([
                "symbol",
                "profile",
                "running",
                "total_pnl_usdt",
                "buys_today",
                "spent_today",
                "last_action",
            ])

            for state in self._states.values():
                writer.writerow([
                    state.symbol,
                    state.profile,
                    state.running,
                    f"{state.total_pnl_usdt:.4f}",
                    state.buys_today,
                    f"{state.spent_today:.2f}",
                    state.last_action,
                ])

        logger.info("📄 Global report generated | %s", file_path)
        return str(file_path)

    def generate_general_report_csv(self) -> str | None:
        if not self._states:
            return None

        reports_dir = Path("reports") / "general"
        reports_dir.mkdir(parents=True, exist_ok=True)

        file_path = reports_dir / "general.csv"

        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)

            writer.writerow([
                "symbol",
                "profile",
                "running",
                "total_pnl_usdt",
                "buys_today",
                "spent_today",
                "last_action",
                "last_update",
            ])

            for state in self._states.values():
                writer.writerow([
                    state.symbol,
                    state.profile,
                    state.running,
                    f"{state.total_pnl_usdt:.4f}",
                    state.buys_today,
                    f"{state.spent_today:.2f}",
                    state.last_action,
                    state.last_update,
                ])

        logger.info("📄 General report generated | %s", file_path)
        return str(file_path)
