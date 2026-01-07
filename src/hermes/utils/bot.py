# src/utils/bot.py
import time
from threading import Thread
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger

from hermes.utils.bot_config import BotConfig
from hermes.providers.binance import Binance
from hermes.providers.Telegram import TelegramNotifier


BOGOTA_TZ = ZoneInfo("America/Bogota")


class Bot(Thread):
    def __init__(
        self,
        config: BotConfig,
        binance: Binance,
        notifier: TelegramNotifier,
    ):
        super().__init__(daemon=True)

        self.config = config
        self.binance = binance
        self.notifier = notifier

        self._running = True

        # Runtime state
        self.open_position_spent = 0.0
        self.buys_today = 0
        self.spent_today = 0.0
        self.current_day = self._day_key()

        # ───────── ARMING STATE ─────────
        self.armed = False
        self.arm_price = None
        self.ARM_PCT = 0.002  # 0.2% ultra conservador

        logger.info(
            f"🧠 Bot initialized | "
            f"symbol={config.symbol} | profile={config.profile}"
        )

    # =========================
    # Thread lifecycle
    # =========================
    def run(self) -> None:
        self.notifier.send(
            f"🤖 Bot started\n"
            f"Symbol: {self.config.symbol}\n"
            f"Profile: {self.config.profile}\n"
            f"Mode: Waiting for market confirmation 🛡️"
        )

        logger.info("🚀 Bot loop started")

        while self._running:
            try:
                self._trade_cycle()
            except Exception as e:
                logger.exception(f"💥 Bot error (recovering): {e}")
                time.sleep(5)

        logger.warning("🛑 Bot loop stopped")

    def stop(self) -> None:
        self._running = False

    # =========================
    # Core trading loop
    # =========================
    def _trade_cycle(self) -> None:
        now = self._now()
        day = self._day_key()

        # Reset daily counters
        if day != self.current_day:
            self.current_day = day
            self.buys_today = 0
            self.spent_today = 0.0
            logger.info(f"🔄 Daily reset | day={day}")

        # ---- balances ----
        usdt = self.binance.get_asset_free("USDT")
        base_qty = self.binance.get_asset_free(self.config.base_asset)

        logger.info(
            f"BALANCES | USDT={usdt:.4f} | "
            f"{self.config.base_asset}={base_qty:.8f} | "
            f"buys_today={self.buys_today}/{self.config.max_buys_per_day} | "
            f"spent_today={self.spent_today:.2f}/{self.config.daily_budget_usdt:.2f}"
        )

        # =========================
        # 1) Manage open position
        # =========================
        if self.open_position_spent > 0:
            self._manage_open_position()
            return

        # =========================
        # 2) ARMING PHASE (ANTI-INSTANT BUY)
        # =========================
        current_price = self.binance.get_price(self.config.symbol)

        if self.arm_price is None:
            self.arm_price = current_price
            logger.info(
                f"🛡️ ARM INIT | reference_price={self.arm_price:.2f}"
            )
            time.sleep(10)
            return

        if not self.armed:
            if current_price >= self.arm_price * (1 + self.ARM_PCT):
                self.armed = True
                logger.info(
                    f"🛡️ ARMED | price moved +{self.ARM_PCT*100:.2f}% | "
                    f"price={current_price:.2f}"
                )
                self.notifier.send(
                    f"🛡️ Market confirmed\n"
                    f"Symbol: {self.config.symbol}\n"
                    f"Trailing enabled ✅"
                )
            else:
                logger.info(
                    f"🛡️ Waiting for confirmation | "
                    f"price={current_price:.2f}"
                )
                time.sleep(10)
                return

        # =========================
        # 3) Risk checks before buy
        # =========================
        if self.buys_today >= self.config.max_buys_per_day:
            time.sleep(10)
            return

        if self.spent_today + self.config.buy_usdt > self.config.daily_budget_usdt:
            time.sleep(10)
            return

        if usdt < self.config.buy_usdt:
            time.sleep(10)
            return

        # =========================
        # 4) Entry signal (SMA — unchanged)
        # =========================
        if not self._entry_signal():
            logger.info("NO SIGNAL | Waiting...")
            time.sleep(10)
            return

        # =========================
        # 5) BUY (same as before)
        # =========================
        self._buy()

    # =========================
    # Trading actions
    # =========================
    def _buy(self) -> None:
        logger.info(
            f"🟢 BUY | {self.config.symbol} | "
            f"amount={self.config.buy_usdt}"
        )

        order = self.binance.buy(
            self.config.symbol,
            self.config.buy_usdt,
        )

        spent = float(order.get("cummulativeQuoteQty", 0.0))
        qty = float(order.get("executedQty", 0.0))

        self.open_position_spent = spent
        self.buys_today += 1
        self.spent_today += spent

        price = spent / qty if qty > 0 else 0.0

        self.notifier.send(
            f"🟢 BUY FILLED\n"
            f"Symbol: {self.config.symbol}\n"
            f"Spent: {spent:.4f} USDT\n"
            f"Qty: {qty:.8f} {self.config.base_asset}\n"
            f"AvgPrice: {price:.2f}"
        )

    def _manage_open_position(self) -> None:
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
        sold_qty = float(order.get("executedQty", 0.0))

        profit = received - self.open_position_spent

        self.notifier.send(
            f"🔴 SELL FILLED\n"
            f"Symbol: {self.config.symbol}\n"
            f"Sold: {sold_qty:.8f} {self.config.base_asset}\n"
            f"Received: {received:.4f} USDT\n"
            f"Result: {profit:+.4f} USDT"
        )

        logger.info(f"📉 Position closed | result={profit:+.4f} USDT")

        # ───────── RESET ARMING AFTER SELL ─────────
        self.open_position_spent = 0.0
        self.armed = False
        self.arm_price = None

        time.sleep(self.config.cooldown_after_sell_seconds)

    # =========================
    # Strategy helpers
    # =========================
    def _entry_signal(self) -> bool:
        klines = self.binance.get_klines(
            symbol=self.config.symbol,
            interval=self.config.kline_interval,
            limit=self.config.kline_limit,
        )

        closes = [float(k[4]) for k in klines]
        if len(closes) < self.config.sma_slow:
            return False

        fast = sum(closes[-self.config.sma_fast:]) / self.config.sma_fast
        slow = sum(closes[-self.config.sma_slow:]) / self.config.sma_slow

        return fast > slow

    # =========================
    # Time helpers
    # =========================
    def _now(self) -> datetime:
        return datetime.now(tz=BOGOTA_TZ)

    def _day_key(self) -> str:
        return self._now().strftime("%Y-%m-%d")
