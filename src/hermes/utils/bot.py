# src/utils/bot.py
import time
from enum import Enum
from threading import Thread
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from loguru import logger
from sqlalchemy import select

from hermes.service.bot_state import BotRuntimeState
from hermes.state.trade_state import load_state, save_state, clear_state
from hermes.utils.bot_config import BotConfig
from hermes.providers.binance import Binance
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from hermes.providers.Telegram import TelegramNotifier
from hermes.reporting.trade_reporter import TradeReporter
from hermes.providers.market_data import MarketData
from hermes.utils.trading_mode import TradingMode
from hermes.utils.adaptive_controller import AdaptiveController
from hermes.persistence.db import SessionLocal
from hermes.persistence.models import Asset, DecisionType, StrategyProfile
from hermes.repository.trade_repository import TradeRepository
from hermes.repository.decision_repository import DecisionRepository
from hermes.repository.performance_repository import PerformanceRepository
from hermes.ai.regime_classifier import RegimeClassifier
from hermes.ai.types import MarketRegime

BOGOTA_TZ = ZoneInfo("America/Bogota")
HEARTBEAT_EVERY_SECONDS = 5.0
VORTEX_ENTRY_THRESHOLD = 0.5
class AIMode(Enum):
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"


AI_MODE = AIMode.SHADOW


class Bot(Thread):
    def __init__(
        self,
        config: BotConfig,
        market_data: MarketData,
        binance: Binance | None,
        state: BotRuntimeState,
        notifier: TelegramNotifier | None = None,
        reporter: TradeReporter | None = None,
        adaptive_controller: AdaptiveController | None = None,
    ):
        super().__init__(daemon=True)

        self.config = config
        self.market = market_data
        self.binance = binance
        self.state = state
        self.notifier = notifier
        self.reporter = reporter
        self.adaptive_controller = adaptive_controller

        self._running = True

        # Internal runtime (logic-only)
        self.open_position_spent = 0.0
        self.buys_today = 0
        self.spent_today = 0.0
        self.current_day = self._day_key()

        # ARMING
        self.armed = False
        self.arm_price = None
        self.ARM_PCT = 0.002  # 0.2%

        self._last_heartbeat = time.monotonic()
        self._last_decision_log_at = 0.0
        self._cycle_regime = None

        self._base_trailing_pct = config.trailing_pct
        self._base_max_buys_per_day = config.max_buys_per_day
        self._base_cooldown_after_sell_seconds = config.cooldown_after_sell_seconds

        # Initial snapshot
        self._set_state(
            bot_id=config.bot_id,
            symbol=config.symbol,
            profile=config.profile,
            trailing_pct=config.trailing_pct,
            running=True,
            last_action="INIT",
            armed=False,
            trailing_enabled=False,
            waiting_for_confirmation=False,
            waiting_for_signal=False,
            awaiting_user_confirmation=False,
            user_confirmed_buy=False,
            vortex_signal_ignored=False,
            capital_skip_notified=False,
            open_position_spent=0.0,
            buys_today=0,
            spent_today=0.0,
            total_pnl_usdt=0.0,
            last_trade_profit_usdt=0.0,
            last_price=None,
            entry_price=None,
            arm_price=None,
            adaptive_state="NORMAL",
            adaptive_reason=None,
            adaptive_max_buys_per_day=None,
            adaptive_cooldown_after_sell_seconds=None,
            ai_mode=AI_MODE.value,
            ai_market_regime=None,
            ai_regime_confidence=None,
            ai_win_rate_60m=None,
            ai_avg_pnl_60m=None,
            ai_pnl_slope_60m=None,
            ai_max_drawdown_60m=None,
            ai_trades_60m=None,
            ai_last_decision=None,
            ai_last_reason=None,
            ai_blocked_by_ai=None,
        )

        logger.info(
            "🧠 Bot initialized | symbol=%s | profile=%s",
            config.symbol,
            config.profile,
        )

    # =========================
    # Safe state setter
    # =========================
    def _set_state(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)

        if hasattr(self.state, "last_update"):
            self.state.last_update = self._now()

    def _effective_max_buys_per_day(self) -> int:
        if self.state.adaptive_max_buys_per_day is not None:
            return self.state.adaptive_max_buys_per_day
        return self.config.max_buys_per_day

    def _effective_cooldown_after_sell_seconds(self) -> float:
        if self.state.adaptive_cooldown_after_sell_seconds is not None:
            return self.state.adaptive_cooldown_after_sell_seconds
        return self.config.cooldown_after_sell_seconds

    def _effective_trailing_pct(self) -> float:
        return self.state.trailing_pct or self.config.trailing_pct

    def apply_adaptive_state(self, adaptive_state: str, reason: str | None = None) -> None:
        if adaptive_state == "DEFENSIVE":
            trailing_pct = max(self._base_trailing_pct * 0.8, 0.001)
            max_buys = max(int(self._base_max_buys_per_day * 0.5), 1)
            cooldown = max(self._base_cooldown_after_sell_seconds * 1.5, 1.0)
            self._set_state(
                adaptive_state="DEFENSIVE",
                adaptive_reason=reason,
                adaptive_max_buys_per_day=max_buys,
                adaptive_cooldown_after_sell_seconds=cooldown,
                trailing_pct=trailing_pct,
            )
            return

        self._set_state(
            adaptive_state="NORMAL",
            adaptive_reason=reason,
            adaptive_max_buys_per_day=None,
            adaptive_cooldown_after_sell_seconds=None,
            trailing_pct=self._base_trailing_pct,
        )

    # =========================
    # Thread lifecycle
    # =========================
    def run(self):
        if self.config.profile == "vortex":
            self._set_state(
                running=True,
                last_action="WAIT_CONFIRMATION",
                waiting_for_confirmation=True,
                waiting_for_signal=False,
                armed=False,
            )
            logger.info(
                "⏳ Bot waiting for arming confirmation | symbol=%s | profile=%s",
                self.config.symbol,
                self.config.profile,
            )
        else:
            self.armed = True
            self._set_state(
                running=True,
                last_action="WAIT_SIGNAL",
                waiting_for_confirmation=False,
                waiting_for_signal=True,
                armed=True,
                live_authorized=True,
                live_authorized_at=time.time(),
            )
            logger.info(
                "✅ Bot armed automatically | symbol=%s | profile=%s",
                self.config.symbol,
                self.config.profile,
            )

        persisted = None
        if self.state.trading_mode == TradingMode.LIVE and self.binance is not None:
            persisted = load_state(self.config.symbol)
        if persisted and persisted.get("in_position"):
            trailing_pct = persisted.get("trailing_pct", self.config.trailing_pct)
            entry_price = persisted.get("entry_price")
            spent = persisted.get("spent_usdt", 0.0)
            max_price = persisted.get("max_price", entry_price)

            if entry_price and spent > 0:
                self.open_position_spent = spent
                self._set_state(
                    last_action="REHYDRATED_TRAILING",
                    entry_price=entry_price,
                    open_position_spent=spent,
                    trailing_enabled=True,
                    waiting_for_signal=False,
                    waiting_for_confirmation=False,
                    trailing_pct=trailing_pct,
                    trailing_max_price=max_price,
                    stop_price=(
                        max_price * (1 - trailing_pct) if max_price is not None else None
                    ),
                )
                logger.warning(
                    "🔁 Rehydrated open position | symbol=%s | entry=%.4f | max=%.4f",
                    self.config.symbol,
                    entry_price,
                    max_price or entry_price,
                )

        while self._running:
            try:
                self._heartbeat()
                self._trade_cycle()
            except Exception:
                logger.exception("💥 Bot error")
                self._set_state(last_action="ERROR")
                time.sleep(5)

        self._set_state(
            running=False,
            last_action="STOPPED",
            waiting_for_confirmation=False,
            waiting_for_signal=False,
            trailing_enabled=False,
        )

    def stop(self):
        self._running = False

    def _heartbeat(self):
        now = time.monotonic()
        if now - self._last_heartbeat >= HEARTBEAT_EVERY_SECONDS:
            logger.info(
                "💓 Loop alive | symbol={} | action={} | armed={} | in_position={}",
                self.state.symbol,
                self.state.last_action,
                self.armed,
                self.open_position_spent > 0,
            )
            self._last_heartbeat = now

    # =========================
    # Core trading loop
    # =========================
    def _trade_cycle(self):
        self._cycle_regime = None
        if self.state.trading_mode != TradingMode.LIVE and self.binance is not None:
            logger.warning("⚠️ Binance injected outside LIVE mode")
        # Daily reset
        day = self._day_key()
        if day != self.current_day:
            self.current_day = day
            self.buys_today = 0
            self.spent_today = 0.0
            self._set_state(
                buys_today=0,
                spent_today=0.0,
            )

        if self.config.profile == "vortex" and self.state.trading_mode != TradingMode.LIVE:
            self._simulate_vortex()
            return

        # =========================
        # Capital snapshot (SAFE)
        # =========================
        if self.state.trading_mode == TradingMode.LIVE:
            if not self.state.live_authorized or self.state.waiting_for_confirmation:
                usdt = 0.0
                base_qty = 0.0
            else:
                self._require_live()
                usdt = self.binance.get_asset_free("USDT")
                base_qty = self.binance.get_asset_free(self.config.base_asset)
        else:
            usdt = self._get_available_capital()
            base_qty = 0.0

        trade_usdt = self._compute_trade_usdt(usdt)

        self._set_state(
            usdt_balance=usdt,
            base_balance=base_qty,
            buys_today=self.buys_today,
            spent_today=self.spent_today,
        )

        # =========================
        # 1) In position → manage trailing
        # =========================
        if self.open_position_spent > 0:
            self._set_state(
                last_action="IN_POSITION",
                trailing_enabled=True,
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            self._manage_open_position()
            return

        if self.config.profile == "vortex" and self.state.trading_mode == TradingMode.LIVE:
            self._vortex_live_cycle(usdt)
            return

        if self.config.profile == "vortex":
            # =========================
            # 2) ARMING (VORTEX ONLY)
            # =========================
            price = float(self.market.get_price(self.config.symbol))
            self._set_state(last_price=price)

            if self.arm_price is None:
                self.arm_price = price
                self._set_state(
                    arm_price=price,
                    last_action="ARM_INIT",
                    waiting_for_confirmation=True,
                    waiting_for_signal=False,
                    armed=False,
                )
                time.sleep(10)
                return

            if not self.armed:
                if price >= self.arm_price * (1 + self.ARM_PCT):
                    self.armed = True
                    self._set_state(
                        armed=True,
                        last_action="ARMED",
                        waiting_for_confirmation=False,
                        waiting_for_signal=False,
                    )
                else:
                    self._set_state(
                        last_action="WAIT_CONFIRMATION",
                        waiting_for_confirmation=True,
                        waiting_for_signal=False,
                        armed=False,
                    )
                    time.sleep(10)
                    return

        # =========================
        # 3) Risk checks
        # =========================
        if (not self.config.disable_max_buys_per_day) and self.buys_today >= self._effective_max_buys_per_day():
            self._set_state(
                last_action="RISK_MAX_BUYS",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if (
            self.state.real_capital_enabled
            and self.state.trading_mode == TradingMode.LIVE
            and self.spent_today + trade_usdt > self.state.real_capital_limit
        ):
            self._set_state(
                last_action="RISK_REAL_CAP_LIMIT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if (not self.config.disable_daily_budget) and self.spent_today + trade_usdt > self.config.daily_budget_usdt:
            self._set_state(
                last_action="RISK_DAILY_BUDGET",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if trade_usdt <= 0 or usdt < trade_usdt:
            self._notify_capital_skip(trade_usdt, usdt)
            self._set_state(
                last_action="RISK_NO_USDT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return
        else:
            if self.state.capital_skip_notified:
                self._set_state(capital_skip_notified=False)

        # =========================
        # 4) Entry signal
        # =========================
        self._set_state(
            last_action="CHECK_SIGNAL",
            waiting_for_signal=True,
            waiting_for_confirmation=False,
        )

        if not self._entry_signal():
            self._set_state(
                last_action="WAIT_SIGNAL",
                waiting_for_signal=True,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        # =========================
        # 5) BUY
        # =========================
        self._buy(trade_usdt)

    # =========================
    # Vortex simulation (paper)
    # =========================
    def _simulate_vortex(self):
        try:
            klines = self.market.get_klines(
                self.config.symbol,
                self.config.kline_interval,
                self.config.kline_limit,
            )
        except Exception as e:
            self._set_state(
                last_action="SIM_DATA_ERROR",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            logger.warning("Vortex data fetch failed: %s", e)
            time.sleep(5)
            return
        if not klines:
            return

        price, score = self._compute_vortex_score(klines)

        if self.state.virtual_qty <= 0:
            self._set_state(
                last_action="SIM_WAIT",
                waiting_for_signal=True,
                waiting_for_confirmation=False,
            )
            if score > VORTEX_ENTRY_THRESHOLD:
                self._set_state(last_signal_ts=time.time())
                qty = self.state.virtual_capital / price
                self._set_state(
                    entry_price=price,
                    virtual_qty=qty,
                    virtual_entry_price=price,
                    virtual_max_price=price,
                    last_action="SIM_ENTRY",
                    waiting_for_signal=False,
                )
            return

        virtual_max_price = max(self.state.virtual_max_price or price, price)
        stop_price = virtual_max_price * (1 - self.config.trailing_pct)
        self._set_state(
            stop_price=stop_price,
            virtual_max_price=virtual_max_price,
            last_action="SIM_IN_POSITION",
            waiting_for_signal=False,
        )

        if price <= stop_price:
            exit_value = self.state.virtual_qty * price
            pnl = exit_value - self.state.virtual_capital

            wins = self.state.wins + (1 if pnl > 0 else 0)
            losses = self.state.losses + (1 if pnl <= 0 else 0)
            trades_count = self.state.trades_count + 1
            virtual_pnl = self.state.virtual_pnl + pnl
            total_win = self.state.total_win + (pnl if pnl > 0 else 0.0)
            total_loss = self.state.total_loss + (abs(pnl) if pnl < 0 else 0.0)
            recent_pnls = (self.state.recent_pnls + [pnl])[-10:]
            virtual_peak_pnl = max(self.state.virtual_peak_pnl, virtual_pnl)
            drawdown = 0.0
            if virtual_peak_pnl > 0:
                drawdown = (virtual_peak_pnl - virtual_pnl) / virtual_peak_pnl
            max_drawdown = max(self.state.max_drawdown, drawdown)
            self._set_state(
                entry_price=None,
                stop_price=None,
                last_action="SIM_EXIT",
                virtual_qty=0.0,
                virtual_entry_price=None,
                virtual_max_price=None,
                virtual_pnl=virtual_pnl,
                virtual_peak_pnl=virtual_peak_pnl,
                trades_count=trades_count,
                wins=wins,
                losses=losses,
                total_win=total_win,
                total_loss=total_loss,
                recent_pnls=recent_pnls,
                max_drawdown=max_drawdown,
            )

            if trades_count >= 30:
                win_rate = wins / trades_count
                if win_rate >= 0.55 and virtual_pnl > 0:
                    self._set_state(trading_mode=TradingMode.ARMED)

    def _vortex_live_cycle(self, usdt: float) -> None:
        try:
            klines = self.market.get_klines(
                self.config.symbol,
                self.config.kline_interval,
                self.config.kline_limit,
            )
        except Exception as e:
            self._set_state(
                last_action="LIVE_DATA_ERROR",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            logger.warning("Vortex live data fetch failed: %s", e)
            time.sleep(5)
            return
        if not klines:
            return

        price, score = self._compute_vortex_score(klines)

        if score <= VORTEX_ENTRY_THRESHOLD:
            self._set_state(
                last_action="WAIT_SIGNAL",
                waiting_for_signal=True,
                waiting_for_confirmation=False,
                awaiting_user_confirmation=False,
                user_confirmed_buy=False,
                vortex_signal_ignored=False,
            )
            time.sleep(10)
            return

        if self.state.vortex_signal_ignored:
            self._set_state(
                last_action="WAIT_SIGNAL",
                waiting_for_signal=True,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if self.state.awaiting_user_confirmation:
            if self.state.user_confirmed_buy:
                self._set_state(
                    last_action="USER_CONFIRMED_BUY",
                    waiting_for_confirmation=False,
                    waiting_for_signal=False,
                    awaiting_user_confirmation=False,
                    user_confirmed_buy=False,
                )
            else:
                self._set_state(
                    last_action="WAIT_CONFIRMATION",
                    waiting_for_confirmation=True,
                    waiting_for_signal=False,
                )
                time.sleep(10)
                return
        else:
            self._set_state(
                last_action="WAIT_CONFIRMATION",
                waiting_for_confirmation=True,
                waiting_for_signal=False,
                awaiting_user_confirmation=True,
                user_confirmed_buy=False,
            )
            if self.notifier is not None:
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ YES",
                                callback_data=f"vortex_signal_yes:{self.config.symbol}",
                            ),
                            InlineKeyboardButton(
                                "❌ NO",
                                callback_data=f"vortex_signal_no:{self.config.symbol}",
                            ),
                        ]
                    ]
                )
                self.notifier.send_ephemeral_sync(
                    text=(
                        "🟣 <b>VORTEX SIGNAL</b>\n"
                        f"{self.config.symbol}\n"
                        f"Score: {score:.2f}\n\n"
                        "¿Confirmar compra?"
                    ),
                    delete_after=30,
                    silent=False,
                    reply_markup=keyboard,
                )
            time.sleep(10)
            return

        signal_ts = self.state.last_signal_ts or time.time()
        if self.state.awaiting_fresh_entry and self.state.live_authorized_at:
            if signal_ts <= self.state.live_authorized_at:
                self._set_state(
                    last_action="WAIT_FRESH_SIGNAL",
                    waiting_for_signal=True,
                )
                time.sleep(10)
                return

        trade_usdt = self._compute_trade_usdt(usdt)

        if (not self.config.disable_max_buys_per_day) and self.buys_today >= self._effective_max_buys_per_day():
            self._set_state(
                last_action="RISK_MAX_BUYS",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if (
            self.state.real_capital_enabled
            and self.spent_today + trade_usdt > self.state.real_capital_limit
        ):
            self._set_state(
                last_action="RISK_REAL_CAP_LIMIT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if (not self.config.disable_daily_budget) and self.spent_today + trade_usdt > self.config.daily_budget_usdt:
            self._set_state(
                last_action="RISK_DAILY_BUDGET",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if trade_usdt <= 0 or usdt < trade_usdt:
            self._notify_capital_skip(trade_usdt, usdt)
            self._set_state(
                last_action="RISK_NO_USDT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return
        else:
            if self.state.capital_skip_notified:
                self._set_state(capital_skip_notified=False)

        self._set_state(
            entry_price=price,
            awaiting_fresh_entry=False,
        )
        self._buy(trade_usdt)

    def _compute_vortex_score(self, klines: list) -> tuple[float, float]:
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]

        price = closes[-1]
        velocity = self._compute_velocity(closes)
        atr = self._compute_atr(highs, lows, closes)
        score = velocity / atr if atr > 0 else 0.0

        if score > VORTEX_ENTRY_THRESHOLD:
            self._set_state(last_signal_ts=time.time())

        self._set_state(
            last_price=price,
            vortex_score=score,
        )
        return price, score

    def _get_available_capital(self) -> float:
        if self.binance is None:
            return self.state.virtual_capital

        if not self.state.live_authorized or self.state.waiting_for_confirmation:
            logger.debug("Capital access blocked: bot not fully armed")
            return 0.0

        self._require_live()
        return self.binance.get_asset_free("USDT")

    def _require_live(self) -> None:
        if (
            self.binance is None
            or self.state.trading_mode != TradingMode.LIVE
            or not self.state.live_authorized
        ):
            raise RuntimeError("🚫 Binance access blocked: not authorized LIVE mode")

    def _compute_velocity(self, prices: list[float], n: int = 5) -> float:
        if len(prices) < n + 1:
            return 0.0
        return (prices[-1] - prices[-n - 1]) / n

    def _compute_atr(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        n: int = 14,
    ) -> float:
        if len(closes) < n + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            trs.append(max(highs[i], closes[i - 1]) - min(lows[i], closes[i - 1]))
        return sum(trs[-n:]) / n

    # =========================
    # Trading actions
    # =========================
    def _buy(self, trade_usdt: float):
        self._require_live()
        order = self.binance.buy(self.config.symbol, trade_usdt)

        spent = float(order.get("cummulativeQuoteQty", 0.0))
        qty = float(order.get("executedQty", 0.0))
        price = spent / qty if qty else 0.0

        self.open_position_spent = spent
        self.buys_today += 1
        self.spent_today += spent

        self._set_state(
            last_action="BUY_FILLED",
            open_position_spent=spent,
            entry_price=price,
            buys_today=self.buys_today,
            spent_today=self.spent_today,
            trailing_enabled=False,
            waiting_for_signal=False,
            waiting_for_confirmation=False,
        )
        if self.reporter is not None:
            self.reporter.record_trade(
                bot_id=self.config.bot_id,
                profile=self.config.profile,
                symbol=self.config.symbol,
                side="BUY",
                price=price,
                qty=qty,
                usdt_spent=spent,
                usdt_received=0.0,
                trade_pnl=0.0,
            )
        save_state(
            self.config.symbol,
            {
                "symbol": self.config.symbol,
                "profile": self.config.profile,
                "in_position": True,
                "entry_price": price,
                "entry_qty": qty,
                "spent_usdt": spent,
                "max_price": price,
                "trailing_pct": self._effective_trailing_pct(),
                "entry_time": datetime.utcnow().isoformat() + "Z",
            },
        )

    def _notify_capital_skip(self, trade_usdt: float, wallet_usdt: float) -> None:
        if self.state.capital_skip_notified:
            return

        capital_allowed = wallet_usdt * self.config.capital_pct
        capital_used = self.spent_today
        capital_remaining = max(capital_allowed - capital_used, 0.0)
        min_trade = self.config.min_trade_usdt

        reason = "Capital exhausted"
        details = f"Remaining: {capital_remaining:.2f} USDT < min_trade_usdt ({min_trade:.2f})"
        if trade_usdt <= 0:
            details = f"Capital allowed: {capital_allowed:.2f} USDT < min_trade_usdt ({min_trade:.2f})"

        self._set_state(capital_skip_notified=True)

        if self.notifier is None:
            return

        self._send_trade_alert(
            text=(
                "❌ <b>BUY SKIPPED</b>\n"
                f"Reason: {reason}\n"
                f"{details}"
            ),
            delete_after=12,
        )

    def _compute_trade_usdt(self, total_usdt: float) -> float:
        if total_usdt <= 0:
            return 0.0

        capital_for_bot = total_usdt * self.config.capital_pct
        trade_usdt = capital_for_bot * self.config.trade_pct

        if trade_usdt < self.config.min_trade_usdt:
            if capital_for_bot >= self.config.min_trade_usdt:
                trade_usdt = self.config.min_trade_usdt
            else:
                return 0.0

        return trade_usdt

    def _manage_open_position(self):
        self._require_live()
        last_saved_max = {"value": self.state.trailing_max_price}

        def _update_trailing_state(snapshot: dict[str, float]) -> None:
            current = snapshot.get("current")
            max_price = snapshot.get("max_price")
            stop_price = snapshot.get("stop_price")
            self._set_state(
                last_price=current,
                stop_price=stop_price,
                trailing_max_price=max_price,
                trailing_enabled=True,
            )
            if max_price is None:
                return
            if last_saved_max["value"] is None or max_price > last_saved_max["value"]:
                persisted = load_state(self.config.symbol)
                if persisted and persisted.get("in_position"):
                    persisted["max_price"] = max_price
                    save_state(self.config.symbol, persisted)
                last_saved_max["value"] = max_price

        result = self.binance.trailing_stop_sell_all_pct(
            symbol=self.config.symbol,
            trailing_pct=self._effective_trailing_pct(),
            poll_seconds=3.0,
            max_hold_seconds_without_new_high=self.config.max_hold_seconds_without_new_high,
            new_high_epsilon_pct=self.config.new_high_epsilon_pct,
            trend_exit_enabled=self.config.trend_exit_enabled,
            trend_sma_period=self.config.trend_sma_period,
            on_update=_update_trailing_state,
            initial_max_price=self.state.trailing_max_price,
        )

        if result:
            self._on_sell(result)

    def _on_sell(self, order: dict) -> None:
        received = float(order.get("cummulativeQuoteQty", 0.0))
        profit = received - self.open_position_spent

        new_total = (self.state.total_pnl_usdt or 0.0) + profit

        self._set_state(
            last_action="SELL_FILLED",
            trailing_enabled=False,
            last_trade_profit_usdt=profit,
            total_pnl_usdt=new_total,
            waiting_for_signal=False,
            waiting_for_confirmation=False,
        )
        exec_qty = float(order.get("executedQty", 0.0))
        avg_price = received / exec_qty if exec_qty else 0.0
        if self.reporter is not None:
            self.reporter.record_trade(
                bot_id=self.config.bot_id,
                profile=self.config.profile,
                symbol=self.config.symbol,
                side="SELL",
                price=avg_price,
                qty=exec_qty,
                usdt_spent=self.open_position_spent,
                usdt_received=received,
                trade_pnl=profit,
            )
        self._persist_real_trade(
            exit_price=avg_price,
            pnl=profit,
            exit_reason="TRAILING_STOP",
        )
        if self.adaptive_controller is not None:
            try:
                self.adaptive_controller.evaluate(self)
            except Exception as e:
                logger.warning("Adaptive evaluation failed: %s", e)
        clear_state(self.config.symbol)

        if self.state.trading_mode == TradingMode.LIVE and self.state.real_capital_enabled:
            if self.state.real_capital_limit > 0:
                drawdown_pct = 0.0
                if new_total < 0:
                    drawdown_pct = abs(new_total) / self.state.real_capital_limit
                self._set_state(real_drawdown_pct=drawdown_pct)

        # Reset position
        self.open_position_spent = 0.0
        self.armed = False
        self.arm_price = None

        # Reflect reset in UI state (optional but helps visuals)
        self._set_state(
            open_position_spent=0.0,
            armed=False,
            arm_price=None,
            entry_price=None,
            stop_price=None,
            trailing_max_price=None,
        )

        time.sleep(self._effective_cooldown_after_sell_seconds())

    def _persist_real_trade(
        self,
        *,
        exit_price: float,
        pnl: float,
        exit_reason: str,
    ) -> None:
        if self.state.trading_mode != TradingMode.LIVE:
            return

        persisted = load_state(self.config.symbol)
        entry_time = None
        entry_price = self.state.entry_price or 0.0
        if persisted:
            entry_price = persisted.get("entry_price", entry_price)
            raw_entry_time = persisted.get("entry_time")
            if isinstance(raw_entry_time, str):
                try:
                    entry_time = datetime.fromisoformat(raw_entry_time.replace("Z", "+00:00"))
                except ValueError:
                    entry_time = None

        if entry_time is None:
            entry_time = datetime.now(timezone.utc)
        elif entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        exit_time = datetime.now(timezone.utc)
        duration_seconds = int((exit_time - entry_time).total_seconds())

        symbol = self.config.symbol
        base_asset = self.config.base_asset or symbol.replace("USDT", "")
        quote_asset = "USDT" if symbol.endswith("USDT") else symbol.replace(base_asset, "")

        try:
            with SessionLocal() as session:
                profile = session.execute(
                    select(StrategyProfile).where(StrategyProfile.name == self.config.profile)
                ).scalars().first()
                if profile is None:
                    profile = StrategyProfile(
                        name=self.config.profile,
                        risk_level=self.config.profile,
                        description="Auto-created profile",
                    )
                    session.add(profile)
                    session.flush()

                asset = session.execute(
                    select(Asset).where(Asset.symbol == symbol)
                ).scalars().first()
                if asset is None:
                    asset = Asset(
                        symbol=symbol,
                        base_asset=base_asset,
                        quote_asset=quote_asset,
                    )
                    session.add(asset)
                    session.flush()

                repo = TradeRepository(session)
                repo.save_real_trade(
                    profile_id=profile.profile_id,
                    asset_id=asset.asset_id,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_price=float(entry_price or 0.0),
                    exit_price=float(exit_price or 0.0),
                    pnl=float(pnl),
                    fees=0.0,
                    duration_seconds=duration_seconds,
                    exit_reason=exit_reason,
                )
        except Exception as e:
            logger.warning("DB trade persist failed: %s", e)

    def _send_trade_alert(self, text: str, delete_after: int) -> None:
        if self.notifier is None:
            return

        def _send():
            try:
                self.notifier.send_ephemeral_sync(
                    text=text,
                    delete_after=delete_after,
                    silent=False,
                )
            except Exception as e:
                logger.warning("Trade alert failed: %s", e)

        Thread(target=_send, daemon=True).start()

    # =========================
    # Strategy helpers
    # =========================
    def _entry_signal(self) -> bool:
        klines = self.market.get_klines(
            self.config.symbol,
            self.config.kline_interval,
            self.config.kline_limit,
        )

        closes = [float(k[4]) for k in klines]
        if len(closes) < self.config.sma_slow:
            return False

        fast = sum(closes[-self.config.sma_fast:]) / self.config.sma_fast
        slow = sum(closes[-self.config.sma_slow:]) / self.config.sma_slow
        current = closes[-1]

        self._set_state(
            sma_fast=fast,
            sma_slow=slow,
            entry_price=current if fast > slow else None,
        )

        signal = fast > slow
        if signal:
            if not self._shadow_regime_check():
                return False
        else:
            self._log_no_trade_decision(reason="no_signal", min_interval_seconds=300.0)

        return signal

    def _shadow_regime_check(self) -> bool:
        try:
            with SessionLocal() as session:
                profile_id, asset_id = self._ensure_profile_asset(session)

                regime = self._get_cycle_regime(session, profile_id, asset_id)

                decision_repo = DecisionRepository(session)
                if AI_MODE == AIMode.ACTIVE and regime == MarketRegime.NO_EDGE:
                    decision_repo.save_decision(
                        asset_id=asset_id,
                        profile_id=profile_id,
                        decision_type=DecisionType.NO_TRADE,
                        regime_detected=regime.value,
                        confidence_score=0.0,
                        reason="blocked_by_regime_classifier",
                    )
                    self._set_state(
                        ai_last_decision=DecisionType.NO_TRADE.value,
                        ai_last_reason="blocked_by_regime_classifier",
                        ai_blocked_by_ai=True,
                    )
                    return False

                decision_repo.save_decision(
                    asset_id=asset_id,
                    profile_id=profile_id,
                    decision_type=DecisionType.ENTER,
                    regime_detected=regime.value,
                    confidence_score=0.0,
                    reason="shadow_mode: regime_classifier",
                )
                self._set_state(
                    ai_last_decision=DecisionType.ENTER.value,
                    ai_last_reason="shadow_mode: regime_classifier",
                    ai_blocked_by_ai=False,
                )
                return True
        except Exception as e:
            logger.warning("Decision log failed: %s", e)
            return True

    def _log_no_trade_decision(self, *, reason: str, min_interval_seconds: float) -> None:
        now = time.monotonic()
        if now - self._last_decision_log_at < min_interval_seconds:
            return
        self._last_decision_log_at = now

        try:
            with SessionLocal() as session:
                profile_id, asset_id = self._ensure_profile_asset(session)
                regime = self._get_cycle_regime(session, profile_id, asset_id)
                decision_repo = DecisionRepository(session)
                decision_repo.save_decision(
                    asset_id=asset_id,
                    profile_id=profile_id,
                    decision_type=DecisionType.NO_TRADE,
                    regime_detected=regime.value,
                    confidence_score=0.0,
                    reason=reason,
                )
                self._set_state(
                    ai_last_decision=DecisionType.NO_TRADE.value,
                    ai_last_reason=reason,
                    ai_blocked_by_ai=False,
                )
        except Exception as e:
            logger.warning("Decision log failed: %s", e)

    def _get_cycle_regime(self, session, profile_id: int, asset_id: int) -> MarketRegime:
        if self._cycle_regime is not None:
            return self._cycle_regime

        perf_repo = PerformanceRepository(session)
        window = perf_repo.get_latest_window(profile_id, asset_id)
        classifier = RegimeClassifier(perf_repo)
        regime = classifier.classify_window(window)
        self._cycle_regime = regime
        self._set_state(
            ai_mode=AI_MODE.value,
            ai_market_regime=regime.value,
            ai_regime_confidence=None,
            ai_win_rate_60m=window.win_rate if window else None,
            ai_avg_pnl_60m=window.avg_pnl if window else None,
            ai_pnl_slope_60m=window.pnl_slope if window else None,
            ai_max_drawdown_60m=window.max_drawdown if window else None,
            ai_trades_60m=window.trades_count if window else None,
        )
        return regime

    def _ensure_profile_asset(self, session) -> tuple[int, int]:
        symbol = self.config.symbol
        base_asset = self.config.base_asset or symbol.replace("USDT", "")
        quote_asset = "USDT" if symbol.endswith("USDT") else symbol.replace(base_asset, "")

        profile = session.execute(
            select(StrategyProfile).where(StrategyProfile.name == self.config.profile)
        ).scalars().first()
        if profile is None:
            profile = StrategyProfile(
                name=self.config.profile,
                risk_level=self.config.profile,
                description="Auto-created profile",
            )
            session.add(profile)
            session.flush()

        asset = session.execute(
            select(Asset).where(Asset.symbol == symbol)
        ).scalars().first()
        if asset is None:
            asset = Asset(
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
            )
            session.add(asset)
            session.flush()

        return profile.profile_id, asset.asset_id

    # =========================
    # Time helpers
    # =========================
    def _now(self):
        return datetime.now(tz=BOGOTA_TZ)

    def _day_key(self):
        return self._now().strftime("%Y-%m-%d")
