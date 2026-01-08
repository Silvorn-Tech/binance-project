# src/utils/bot.py
import time
from threading import Thread
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger

from hermes.service.bot_state import BotRuntimeState
from hermes.utils.bot_config import BotConfig
from hermes.providers.binance import Binance

BOGOTA_TZ = ZoneInfo("America/Bogota")
HEARTBEAT_EVERY_SECONDS = 5.0


class Bot(Thread):
    def __init__(
        self,
        config: BotConfig,
        binance: Binance,
        state: BotRuntimeState,
    ):
        super().__init__(daemon=True)

        self.config = config
        self.binance = binance
        self.state = state

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

        # Initial snapshot
        self._set_state(
            symbol=config.symbol,
            profile=config.profile,
            base_asset=config.base_asset,
            trailing_pct=config.trailing_pct,
            running=True,
            last_action="INIT",
            armed=False,
            total_pnl_usdt=0.0,
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

    # =========================
    # Thread lifecycle
    # =========================
    def run(self):
        self._set_state(
            running=True,
            last_action="WAITING_CONFIRMATION",
            waiting_for_confirmation=True,
            armed=False,
        )

        logger.info(
            "⏳ Bot waiting for arming confirmation | symbol=%s | profile=%s",
            self.config.symbol,
            self.config.profile,
        )

        while self._running:
            try:
                self._heartbeat()
                self._trade_cycle()
            except Exception:
                logger.exception("💥 Bot error")
                self._set_state(last_action="ERROR")
                time.sleep(5)

        self._set_state(running=False, last_action="STOPPED")

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
        # Daily reset
        day = self._day_key()
        if day != self.current_day:
            self.current_day = day
            self.buys_today = 0
            self.spent_today = 0.0

        usdt = self.binance.get_asset_free("USDT")
        base_qty = self.binance.get_asset_free(self.config.base_asset)

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

        # =========================
        # 2) ARMING
        # =========================
        price = float(self.binance.get_price(self.config.symbol))
        self._set_state(last_price=price)

        if self.arm_price is None:
            self.arm_price = price
            self._set_state(
                arm_price=price,
                last_action="ARM_INIT",
                waiting_for_confirmation=True,
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
                )
            else:
                self._set_state(
                    last_action="WAIT_CONFIRMATION",
                    waiting_for_confirmation=True,
                )
                time.sleep(10)
                return

        # =========================
        # 3) Risk checks
        # =========================
        if self.buys_today >= self.config.max_buys_per_day:
            self._set_state(last_action="RISK_MAX_BUYS")
            time.sleep(10)
            return

        if self.spent_today + self.config.buy_usdt > self.config.daily_budget_usdt:
            self._set_state(last_action="RISK_DAILY_BUDGET")
            time.sleep(10)
            return

        if usdt < self.config.buy_usdt:
            self._set_state(last_action="RISK_NO_USDT")
            time.sleep(10)
            return

        # =========================
        # 4) Entry signal
        # =========================
        self._set_state(last_action="CHECK_SIGNAL", waiting_for_signal=True)

        if not self._entry_signal():
            self._set_state(last_action="WAIT_SIGNAL")
            time.sleep(10)
            return

        # =========================
        # 5) BUY
        # =========================
        self._buy()

    # =========================
    # Trading actions
    # =========================
    def _buy(self):
        order = self.binance.buy(self.config.symbol, self.config.buy_usdt)

        spent = float(order["cummulativeQuoteQty"])
        qty = float(order["executedQty"])
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
        )

    def _manage_open_position(self):
        result = self.binance.trailing_stop_sell_all_pct(
            symbol=self.config.symbol,
            trailing_pct=self.config.trailing_pct,
            poll_seconds=3.0,
            max_hold_seconds_without_new_high=self.config.max_hold_seconds_without_new_high,
            trend_exit_enabled=self.config.trend_exit_enabled,
            trend_sma_period=self.config.trend_sma_period,
        )

        if result:
            self._on_sell(result)

    def _on_sell(self, order: dict) -> None:
        received = float(order.get("cummulativeQuoteQty", 0.0))
        profit = received - self.open_position_spent

        # Accumulated PnL
        self.state.total_pnl_usdt += profit
        self.state.last_trade_profit_usdt = profit

        self._set_state(
            last_action="SELL_FILLED",
            trailing_enabled=False,
        )

        # Reset position
        self.open_position_spent = 0.0
        self.armed = False
        self.arm_price = None

        time.sleep(self.config.cooldown_after_sell_seconds)

    # =========================
    # Strategy helpers
    # =========================
    def _entry_signal(self) -> bool:
        klines = self.binance.get_klines(
            self.config.symbol,
            self.config.kline_interval,
            self.config.kline_limit,
        )

        closes = [float(k[4]) for k in klines]
        if len(closes) < self.config.sma_slow:
            return False

        fast = sum(closes[-self.config.sma_fast:]) / self.config.sma_fast
        slow = sum(closes[-self.config.sma_slow:]) / self.config.sma_slow

        self._set_state(sma_fast=fast, sma_slow=slow)
        return fast > slow

    # =========================
    # Time helpers
    # =========================
    def _now(self):
        return datetime.now(tz=BOGOTA_TZ)

    def _day_key(self):
        return self._now().strftime("%Y-%m-%d")
