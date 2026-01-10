import time
from dataclasses import replace
from loguru import logger
from datetime import datetime
from pathlib import Path
import csv

from hermes.service.bot_builder import BotBuilder
from hermes.config.bot_config_store import save_config
from hermes.reporting.trade_reporter import TradeReporter
from hermes.utils.adaptive_controller import AdaptiveController
from hermes.reporting.post_mortem_audit import PostMortemAuditor
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
        self.reporter = TradeReporter()
        self.adaptive_controller = AdaptiveController(self.reporter)

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

        initial_mode = TradingMode.AI

        state = BotRuntimeState(
            bot_id=config.bot_id,
            symbol=config.symbol,
            profile=config.profile,
            base_asset=config.base_asset,
            trailing_pct=config.trailing_pct,
            config=config,
            trading_mode=initial_mode,
            ai_enabled=True,
            ai_snapshot_started_at=time.time(),
            ai_mode="ADM",
        )

        if config.profile in {"sentinel", "equilibrium"} and state.trading_mode == TradingMode.LIVE:
            state.read_only = True
            state.read_only_until = time.time() + 60 * 60
            state.read_only_reason = "warmup_60m"

        if state.trading_mode == TradingMode.LIVE:
            state.live_authorized = True
            state.live_authorized_at = time.time()
            state.waiting_for_confirmation = False
            state.armed = True
            state.waiting_for_signal = True


        self._states[symbol] = state

        save_config(config)

        live_binance = self.binance if state.trading_mode == TradingMode.LIVE else None
        bot = Bot(
            config=config,
            market_data=self.market_data,
            binance=live_binance,
            state=state,
            notifier=self.notifier,
            reporter=self.reporter,
            adaptive_controller=self.adaptive_controller,
        )

        bot.start()
        self._bots[symbol] = bot

    def get_bot_state_by_id(self, bot_id: str) -> BotRuntimeState | None:
        for state in self._states.values():
            if state.bot_id == bot_id:
                return state
        return None

    def restart_bot_with_config(self, bot_id: str, config: BotConfig) -> None:
        state = self.get_bot_state_by_id(bot_id)
        if not state:
            raise RuntimeError(f"No running bot for {bot_id}")

        prev_message_id = state.telegram_message_id

        self.stop_bot(state.symbol)
        save_config(config)
        self.start_bot_from_config(config)

        if prev_message_id is not None:
            new_state = self.get_bot_state_by_id(bot_id)
            if new_state:
                new_state.telegram_message_id = prev_message_id
                new_state.last_dashboard_hash = None
                new_state.last_dashboard_update = 0.0

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

    def enable_ai_mode(self, symbol: str) -> None:
        bot = self._bots.get(symbol.upper())
        if not bot:
            raise RuntimeError(f"No running bot for {symbol}")
        if bot.open_position_spent <= 0:
            bot.binance = None
        bot.state.trading_mode = TradingMode.AI
        bot.state.ai_enabled = True
        bot.state.ai_snapshot_started_at = time.time()
        bot.state.ai_recommendation = None
        bot.state.ai_confidence = None
        bot.state.ai_last_decision_at = None
        bot.state.ai_mode = "ADM"
        bot.state.live_authorized = False
        bot.state.real_capital_enabled = False
        bot.state.ai_pending_recommendation = False
        bot.state.ai_last_recommendation_id = None
        bot.state.ai_last_recommendation_message_id = None

    def disable_ai_mode(self, symbol: str) -> None:
        bot = self._bots.get(symbol.upper())
        if not bot:
            raise RuntimeError(f"No running bot for {symbol}")
        bot.state.ai_enabled = False
        bot.state.ai_snapshot_started_at = None
        bot.state.ai_mode = "SHADOW"

    def authorize_recovery(self, symbol: str) -> BotRuntimeState:
        bot = self._bots.get(symbol.upper())
        if not bot:
            raise RuntimeError(f"No running bot for {symbol}")

        bot.binance = self.binance
        bot.state.trading_mode = TradingMode.LIVE
        bot.state.live_authorized = True
        bot.state.live_authorized_at = time.time()
        bot.state.read_only = True
        bot.state.read_only_until = None
        bot.state.read_only_reason = "recovery_close_only"
        bot.state.ai_enabled = False
        bot.state.ai_mode = "SHADOW"
        bot.rehydrate_open_position()

        return bot.state

    def enter_live_from_ai(self, symbol: str, *, allow_override: bool = False) -> BotRuntimeState:
        state = self.get_bot_state(symbol)
        if not state or not state.config:
            raise RuntimeError(f"No running bot for {symbol}")

        recommendation = state.ai_recommendation or {}
        if not recommendation:
            raise RuntimeError("No AI recommendation available")

        profile_map = {
            "SENTINEL": "sentinel",
            "EQUILIBRIUM": "equilibrium",
            "VORTEX": "vortex",
        }
        recommended_profile_raw = str(recommendation.get("recommended_profile", "")).upper()
        if recommended_profile_raw == "NO_TRADE":
            raise RuntimeError("AI recommended NO_TRADE")
        recommended_profile = profile_map.get(recommended_profile_raw)
        if recommended_profile is None:
            raise RuntimeError("Invalid AI recommended profile")

        if allow_override:
            target_profile = state.profile
            config = state.config
            override_flag = True
            override_reason = "user_confirmed"
        else:
            target_profile = recommended_profile
            base_asset = state.base_asset or state.config.base_asset
            config = (
                BotBuilder()
                .with_symbol(symbol, base_asset)
                .with_profile(target_profile)
                .with_defaults()
                .build()
            )
            config = replace(
                config,
                bot_id=state.bot_id,
                kline_interval=state.config.kline_interval,
                kline_limit=state.config.kline_limit,
                cooldown_after_sell_seconds=state.config.cooldown_after_sell_seconds,
                trend_exit_enabled=state.config.trend_exit_enabled,
                trend_sma_period=state.config.trend_sma_period,
                max_hold_seconds_without_new_high=state.config.max_hold_seconds_without_new_high,
            )
            config = self._apply_ai_risk_caps(
                current_config=state.config,
                recommended_config=config,
            )
            override_flag = False
            override_reason = None

        self.restart_bot_with_config(state.bot_id, config)
        new_state = self.get_bot_state_by_id(state.bot_id)
        if not new_state:
            new_state = self.get_bot_state(symbol)
        if not new_state:
            raise RuntimeError("Failed to load bot after AI transition")

        new_state.trading_mode = TradingMode.LIVE
        new_state.live_authorized = True
        new_state.live_authorized_at = time.time()
        new_state.awaiting_fresh_entry = True
        new_state.read_only = True
        new_state.read_only_until = time.time() + 10 * 60
        new_state.read_only_reason = "ai_warmup"
        new_state.ai_enabled = False
        new_state.ai_mode = "SHADOW"
        new_state.ai_override = override_flag
        new_state.ai_override_reason = override_reason
        new_state.ai_pending_recommendation = False
        new_state.ai_last_recommendation_id = None
        new_state.ai_last_recommendation_message_id = None
        self.enable_live(symbol)

        return new_state

    def _apply_ai_risk_caps(
        self,
        *,
        current_config: BotConfig,
        recommended_config: BotConfig,
    ) -> BotConfig:
        capped_capital_pct = min(current_config.capital_pct, recommended_config.capital_pct)
        capped_trade_pct = min(current_config.trade_pct, recommended_config.trade_pct)
        capped_max_buys = min(current_config.max_buys_per_day, recommended_config.max_buys_per_day)
        capped_daily_budget = min(current_config.daily_budget_usdt, recommended_config.daily_budget_usdt)
        capped_trailing = max(current_config.trailing_pct, recommended_config.trailing_pct)

        disable_max_buys = recommended_config.disable_max_buys_per_day
        if current_config.disable_max_buys_per_day is False:
            disable_max_buys = False

        disable_daily_budget = recommended_config.disable_daily_budget
        if current_config.disable_daily_budget is False:
            disable_daily_budget = False

        return replace(
            recommended_config,
            capital_pct=capped_capital_pct,
            trade_pct=capped_trade_pct,
            max_buys_per_day=capped_max_buys,
            daily_budget_usdt=capped_daily_budget,
            trailing_pct=capped_trailing,
            disable_max_buys_per_day=disable_max_buys,
            disable_daily_budget=disable_daily_budget,
        )

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

    def get_trade_report_csv(self) -> str | None:
        file_path = self.reporter.file_path
        if not file_path.exists():
            return None
        return str(file_path)

    def adaptive_review(self, bot_id: str, limit: int = 10) -> str:
        trades = self.reporter.get_recent_trades(
            bot_id=bot_id,
            limit=limit,
            side="SELL",
        )
        if not trades:
            return "No trades found."

        metrics = self.adaptive_controller.compute_metrics(trades)
        state = self.get_bot_state_by_id(bot_id)
        current_state = state.adaptive_state if state else "NORMAL"
        expected_state, reason = self.adaptive_controller.decide_target_state(
            metrics,
            current_state=current_state,
        )

        flip_rate = "—"
        if metrics.flip_rate is not None:
            flip_rate = f"{metrics.flip_rate:.2f}"

        lines = [
            f"Bot: {bot_id}",
            f"Profile: {state.profile if state else '—'}",
            "",
            f"Trades analyzed: {metrics.total_trades}",
            f"Win rate: {metrics.win_rate:.2f}",
            f"Cumulative PnL: {metrics.cumulative_pnl:+.4f}",
            f"Drawdown: {metrics.drawdown_pct * 100:.2f}%",
            f"Negative streak: {metrics.negative_streak}",
            f"Flip rate: {flip_rate}",
            "",
            f"Expected state: {expected_state}",
            "Reason:",
            f"- {reason or '—'}",
        ]
        return "\n".join(lines)

    def post_mortem(self, bot_id: str, limit: int = 30) -> str:
        auditor = PostMortemAuditor(self.reporter, self.adaptive_controller)
        return auditor.generate_summary(bot_id=bot_id, limit=limit)

    def get_last_trades(self, bot_id: str, limit: int = 5) -> list[dict]:
        return self.reporter.get_last_trades(bot_id=bot_id, limit=limit)
