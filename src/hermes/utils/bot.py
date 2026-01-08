# src/utils/bot.py
import time
from threading import Thread
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger

from hermes.service.bot_state import BotRuntimeState
from hermes.utils.bot_config import BotConfig
from hermes.providers.binance import Binance
from hermes.providers.market_data import MarketData
from hermes.utils.trading_mode import TradingMode

BOGOTA_TZ = ZoneInfo("America/Bogota")
HEARTBEAT_EVERY_SECONDS = 5.0
VORTEX_ENTRY_THRESHOLD = 0.5


class Bot(Thread):
    def __init__(
        self,
        config: BotConfig,
        market_data: MarketData,
        binance: Binance | None,
        state: BotRuntimeState,
    ):
        super().__init__(daemon=True)

        self.config = config
        self.market = market_data
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
            trailing_pct=config.trailing_pct,
            running=True,
            last_action="INIT",
            armed=False,
            trailing_enabled=False,
            waiting_for_confirmation=False,
            waiting_for_signal=False,
            open_position_spent=0.0,
            buys_today=0,
            spent_today=0.0,
            total_pnl_usdt=0.0,
            last_trade_profit_usdt=0.0,
            last_price=None,
            entry_price=None,
            arm_price=None,
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
        if self.buys_today >= self.config.max_buys_per_day:
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
            and self.spent_today + self.config.buy_usdt > self.state.real_capital_limit
        ):
            self._set_state(
                last_action="RISK_REAL_CAP_LIMIT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if self.spent_today + self.config.buy_usdt > self.config.daily_budget_usdt:
            self._set_state(
                last_action="RISK_DAILY_BUDGET",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if usdt < self.config.buy_usdt:
            self._set_state(
                last_action="RISK_NO_USDT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

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
        self._buy()

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

        self._set_state(
            last_action="CHECK_SIGNAL",
            waiting_for_signal=True,
            waiting_for_confirmation=False,
        )

        if score <= VORTEX_ENTRY_THRESHOLD:
            self._set_state(
                last_action="WAIT_SIGNAL",
                waiting_for_signal=True,
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

        if self.buys_today >= self.config.max_buys_per_day:
            self._set_state(
                last_action="RISK_MAX_BUYS",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if (
            self.state.real_capital_enabled
            and self.spent_today + self.config.buy_usdt > self.state.real_capital_limit
        ):
            self._set_state(
                last_action="RISK_REAL_CAP_LIMIT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if self.spent_today + self.config.buy_usdt > self.config.daily_budget_usdt:
            self._set_state(
                last_action="RISK_DAILY_BUDGET",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        if usdt < self.config.buy_usdt:
            self._set_state(
                last_action="RISK_NO_USDT",
                waiting_for_signal=False,
                waiting_for_confirmation=False,
            )
            time.sleep(10)
            return

        self._set_state(
            entry_price=price,
            awaiting_fresh_entry=False,
        )
        self._buy()

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
    def _buy(self):
        self._require_live()
        order = self.binance.buy(self.config.symbol, self.config.buy_usdt)

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

    def _manage_open_position(self):
        self._require_live()
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

        new_total = (self.state.total_pnl_usdt or 0.0) + profit

        self._set_state(
            last_action="SELL_FILLED",
            trailing_enabled=False,
            last_trade_profit_usdt=profit,
            total_pnl_usdt=new_total,
            waiting_for_signal=False,
            waiting_for_confirmation=False,
        )

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
        )

        time.sleep(self.config.cooldown_after_sell_seconds)

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

        return fast > slow

    # =========================
    # Time helpers
    # =========================
    def _now(self):
        return datetime.now(tz=BOGOTA_TZ)

    def _day_key(self):
        return self._now().strftime("%Y-%m-%d")
