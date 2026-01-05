from binance.client import Client
from decimal import Decimal, ROUND_DOWN
from loguru import logger
import time
from typing import Optional, Callable, Any


class Binance:
    MIN_TRADE_USDT = 7.0

    def __init__(self, api_key: str, api_secret: str):
        logger.debug("Initializing Binance client")
        self._client = Client(api_key, api_secret)
        self._symbol_info_cache: dict[str, dict] = {}

    # =========================
    # Low-level helpers
    # =========================

    def _get_account(self) -> dict:
        return self._client.get_account()

    def _get_symbol_info(self, symbol: str) -> dict:
        symbol = symbol.upper()
        if symbol not in self._symbol_info_cache:
            self._symbol_info_cache[symbol] = self._client.get_symbol_info(symbol)
        return self._symbol_info_cache[symbol]

    def _get_filter(self, symbol: str, filter_type: str) -> dict:
        info = self._get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f.get("filterType") == filter_type:
                return f
        raise ValueError(f"Filter {filter_type} not found for {symbol}")

    def _get_base_asset(self, symbol: str) -> str:
        info = self._get_symbol_info(symbol)
        return info["baseAsset"]

    # =========================
    # Balances / Prices
    # =========================

    def get_asset_free(self, asset: str) -> float:
        asset = asset.upper()
        account = self._get_account()
        for b in account["balances"]:
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def get_usdt_free(self) -> float:
        logger.info("Fetching USDT free balance")
        usdt = self.get_asset_free("USDT")
        if usdt > 0:
            logger.info(f"USDT balance found: {usdt}")
        else:
            logger.warning("USDT balance not found, returning 0.0")
        return usdt

    def get_bnb_free(self) -> float:
        logger.info("Fetching BNB free balance")
        bnb = self.get_asset_free("BNB")
        if bnb > 0:
            logger.info(f"BNB balance found: {bnb}")
        else:
            logger.warning("BNB balance not found, returning 0.0")
        return bnb

    def get_price(self, symbol: str) -> float:
        symbol = symbol.upper()
        return float(self._client.get_symbol_ticker(symbol=symbol)["price"])

    def get_klines(self, symbol: str, interval: str, limit: int = 50) -> list:
        symbol = symbol.upper()
        return self._client.get_klines(symbol=symbol, interval=interval, limit=limit)

    # =========================
    # Exchange adjustments
    # =========================

    def _adjust_qty(self, symbol: str, qty: float) -> float:
        """
        Adjust quantity to LOT_SIZE stepSize using Decimal to avoid float issues.
        """
        symbol = symbol.upper()
        lot_size = self._get_filter(symbol, "LOT_SIZE")

        step = Decimal(lot_size["stepSize"])
        q = Decimal(str(qty))

        adjusted = (q / step).to_integral_value(rounding=ROUND_DOWN) * step
        return float(adjusted)

    def _adjust_price(self, symbol: str, price: float) -> float:
        """
        Adjust price to PRICE_FILTER tickSize (required for STOP_LOSS_LIMIT and LIMIT orders).
        """
        symbol = symbol.upper()
        pf = self._get_filter(symbol, "PRICE_FILTER")

        tick = Decimal(pf["tickSize"])
        p = Decimal(str(price))

        adjusted = (p / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        return float(adjusted)

    def _get_min_notional(self, symbol: str) -> float:
        """
        Return exchange min notional if present (NOTIONAL or MIN_NOTIONAL filters).
        """
        symbol = symbol.upper()
        info = self._get_symbol_info(symbol)

        for f in info.get("filters", []):
            if f.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL"):
                return float(f.get("minNotional", 0.0))

        # Some symbols may not return it in spot filters; keep safe fallback.
        logger.warning(f"No NOTIONAL/MIN_NOTIONAL filter found for {symbol}. Using 0.0 fallback.")
        return 0.0

    # =========================
    # Trade validation
    # =========================

    def can_trade(self, symbol: str, qty: float, enforce_user_min: bool = False) -> tuple[bool, str]:
        symbol = symbol.upper()

        if qty <= 0:
            return False, "Quantity must be greater than zero"

        adjusted_qty = self._adjust_qty(symbol, qty)
        if adjusted_qty <= 0:
            return False, "Quantity too small after LOT_SIZE adjustment"

        price = float(self._client.get_symbol_ticker(symbol=symbol)["price"])
        notional = adjusted_qty * price

        min_exchange = self._get_min_notional(symbol)

        # IMPORTANT:
        # - For SELL/STOP orders you should NOT force your internal MIN_TRADE_USDT
        # - Only enforce it when you explicitly want it (e.g., on BUY decisions)
        min_required = max(min_exchange, self.MIN_TRADE_USDT) if enforce_user_min else min_exchange

        if notional < min_required:
            return False, f"Notional too small ({notional:.8f} < {min_required})"

        return True, ""


    def _require_tradeable_qty(
        self,
        symbol: str,
        qty: float,
        context: str,
        ignore_min_trade: bool = False,
    ) -> str:
        """
        Validate, adjust and format quantity for trading.

        - ignore_min_trade=True  -> only Binance rules (LOT_SIZE + NOTIONAL)
        - ignore_min_trade=False -> Binance rules + user MIN_TRADE_USDT
        """
        symbol = symbol.upper()

        ok, reason = self.can_trade(
            symbol,
            qty,
            enforce_user_min=not ignore_min_trade
        )
        if not ok:
            raise ValueError(f"{context} skipped: {reason}")

        adjusted_qty = self._adjust_qty(symbol, qty)
        if adjusted_qty <= 0.0:
            raise ValueError(
                f"{context} skipped: Quantity too small after LOT_SIZE adjustment"
            )

        return f"{adjusted_qty:.8f}"


    # =========================
    # Trading
    # =========================

    def buy(self, symbol: str, usdt: float) -> dict:
        """
        Buy crypto using USDT at market price.
        Uses quoteOrderQty so it spends up to `usdt`.
        """
        symbol = symbol.upper()
        if usdt < self.MIN_TRADE_USDT:
            raise ValueError(f"USDT amount too small ({usdt}). Minimum is {self.MIN_TRADE_USDT} USDT.")

        logger.info(f"BUY | symbol={symbol} | usdt={usdt}")

        order = self._client.create_order(
            symbol=symbol,
            side="BUY",
            type="MARKET",
            quoteOrderQty=usdt,
        )

        logger.success(
            f"ORDER FILLED | symbol={symbol} | "
            f"spent={order.get('cummulativeQuoteQty')} USDT | "
            f"received={order.get('executedQty')}"
        )
        return order

    def sell(self, symbol: str, usdt: float) -> dict:
        """
        Sell enough base asset to receive approximately `usdt` (market).
        """
        symbol = symbol.upper()
        logger.info(f"SELL FOR USDT | symbol={symbol} | target_usdt={usdt}")

        price = self.get_price(symbol)
        raw_qty = usdt / price

        qty_str = self._require_tradeable_qty(
            symbol,
            raw_qty,
            context="Sell",
            ignore_min_trade=True
        )

        logger.info(f"SELL | symbol={symbol} | qty={qty_str}")

        order = self._client.create_order(
            symbol=symbol,
            side="SELL",
            type="MARKET",
            quantity=qty_str,
        )

        logger.success(
            f"SELL FILLED | sold={order.get('executedQty')} | "
            f"received={order.get('cummulativeQuoteQty')} USDT"
        )
        return order

    def sell_all(self, symbol: str) -> dict:
        """
        Sell ALL free base asset balance (market).
        Uses only Binance exchange rules (LOT_SIZE + NOTIONAL).
        """
        symbol = symbol.upper()
        base_asset = self._get_base_asset(symbol)

        # 1. Get real free balance (auto-sync with app trades)
        qty = self.get_asset_free(base_asset)
        if qty <= 0.0:
            raise ValueError(f"No free balance to sell for {base_asset}")

        # 2. Validate & adjust quantity using exchange filters only
        qty_str = self._require_tradeable_qty(
            symbol,
            qty,
            context="Sell all",
            ignore_min_trade=True  # 🔑 KEY CHANGE
        )

        logger.info(f"SELL ALL | symbol={symbol} | qty={qty_str}")

        # 3. Market sell
        order = self._client.create_order(
            symbol=symbol,
            side="SELL",
            type="MARKET",
            quantity=qty_str,
        )

        logger.success(
            f"SELL ALL FILLED | sold={order.get('executedQty')} | "
            f"received={order.get('cummulativeQuoteQty')} USDT"
        )

        return order


    def safe_sell_all(self, symbol: str) -> dict | None:
        """
        Safe wrapper that never raises for min-notional / qty problems.
        """
        try:
            return self.sell_all(symbol)
        except ValueError as e:
            logger.warning(f"SELL ALL skipped: {e}")
            return None

    # =========================
    # Stop Loss (optional)
    # =========================

    def stop_loss(self, symbol: str, stop_price: float, limit_price: float) -> dict:
        """
        Place a STOP_LOSS_LIMIT order for ALL available base asset.
        Prices are adjusted to tickSize.
        Uses ONLY Binance exchange rules (not MIN_TRADE_USDT).
        """
        symbol = symbol.upper()
        base_asset = self._get_base_asset(symbol)

        # 1. Get real free balance (auto-syncs with manual app trades)
        qty = self.get_asset_free(base_asset)
        if qty <= 0.0:
            raise ValueError(f"No free balance to protect for {base_asset}")

        # 2. Validate quantity using exchange rules only
        qty_str = self._require_tradeable_qty(
            symbol,
            qty,
            context="Stop loss",
            ignore_min_trade=True  # 🔑 KEY CHANGE
        )

        # 3. Adjust prices to tickSize
        stop_adj = self._adjust_price(symbol, stop_price)
        limit_adj = self._adjust_price(symbol, limit_price)

        logger.info(
            f"PLACING STOP LOSS | {symbol} | "
            f"qty={qty_str} | stop={stop_adj} | limit={limit_adj}"
        )

        # 4. Place STOP_LOSS_LIMIT order
        order = self._client.create_order(
            symbol=symbol,
            side="SELL",
            type="STOP_LOSS_LIMIT",
            quantity=qty_str,
            stopPrice=f"{stop_adj}",
            price=f"{limit_adj}",
            timeInForce="GTC",
        )

        logger.success(
            f"STOP LOSS PLACED | qty={qty_str} | stop={stop_adj} | limit={limit_adj}"
        )

        return order


    def safe_stop_loss_pct(self, symbol: str, stop_pct: float = 0.01, limit_pct: float = 0.011) -> dict | None:
        """
        Safe STOP_LOSS_LIMIT using percentages below current market.
        Adjusts prices to tickSize.
        Returns order dict if placed, else None.
        """
        symbol = symbol.upper()
        base_asset = self._get_base_asset(symbol)

        qty = self.get_asset_free(base_asset)
        if qty <= 0:
            logger.warning(f"STOP LOSS skipped: no {base_asset} free balance")
            return None

        current = self.get_price(symbol)

        try:
            qty_str = self._require_tradeable_qty(
                symbol,
                qty,
                context="Safe stop loss",
                ignore_min_trade=True
            )
        except ValueError as e:
            logger.warning(str(e))
            return None


        stop_price = current * (1 - stop_pct)
        limit_price = current * (1 - limit_pct)

        stop_adj = self._adjust_price(symbol, stop_price)
        limit_adj = self._adjust_price(symbol, limit_price)

        logger.info(
            f"STOP LOSS | {symbol} | qty={qty_str} | current={current:.2f} | "
            f"stop={stop_adj} | limit={limit_adj}"
        )

        try:
            order = self._client.create_order(
                symbol=symbol,
                side="SELL",
                type="STOP_LOSS_LIMIT",
                quantity=qty_str,
                stopPrice=f"{stop_adj}",
                price=f"{limit_adj}",
                timeInForce="GTC",
            )
            logger.success(f"STOP LOSS PLACED | orderId={order.get('orderId')}")
            return order
        except Exception as e:
            logger.warning(f"STOP LOSS failed (safe): {e}")
            return None

    # =========================
    # Conversions
    # =========================

    def convert_dust_to_bnb(self, asset: str) -> dict:
        asset = asset.upper()
        logger.info(f"CONVERT DUST TO BNB | asset={asset}")
        result = self._client.transfer_dust(asset=asset)
        logger.success(f"DUST CONVERTED | asset={asset} | result={result}")
        return result

    def bnb_to_btc(self) -> dict:
        symbol = "BNBBTC"
        logger.info("Converting BNB to BTC")

        bnb_qty = self.get_asset_free("BNB")
        if bnb_qty <= 0.0:
            raise ValueError("No free BNB to convert")

        qty_str = self._require_tradeable_qty(
            symbol,
            bnb_qty,
            context="BNB to BTC",
            ignore_min_trade=True
        )

        logger.info(f"BNB → BTC | qty={qty_str}")

        order = self._client.create_order(
            symbol=symbol,
            side="SELL",
            type="MARKET",
            quantity=qty_str,
        )

        logger.success(
            f"BNB → BTC FILLED | sold={order.get('executedQty')} BNB | "
            f"received={order.get('cummulativeQuoteQty')} BTC"
        )
        return order

    # =========================
    # Trailing Stop (bot-managed)
    # =========================

    def _emit_sell(reason: str, order: dict, extra: dict, on_sell: Optional[Callable[[str, dict, dict], Any]] = None) -> None:
        if on_sell is None:
            return
        try:
            on_sell(reason, order, extra)
        except Exception as e:
            logger.warning(f"on_sell callback failed: {e}")

    def is_price_overextended(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 100,
        sma_period: int = 50,
        max_deviation_pct: float = 0.02,  # 2%
    ) -> bool:
        """
        Returns True if price is too far ABOVE SMA (overextended).
        """
        klines = self.get_klines(symbol=symbol, interval=interval, limit=limit)
        closes = [float(k[4]) for k in klines]

        if len(closes) < sma_period:
            return False

        sma_val = sum(closes[-sma_period:]) / sma_period
        current = closes[-1]

        deviation = (current - sma_val) / sma_val

        logger.info(
            f"OVEREXT CHECK | {symbol} | price={current:.2f} | "
            f"SMA{sma_period}={sma_val:.2f} | dev={deviation*100:.2f}%"
        )

        return deviation > max_deviation_pct



    def trailing_stop_sell_all_pct(
        self,
        symbol: str,
        trailing_pct: float = 0.01,
        poll_seconds: float = 3.0,
        min_hold_seconds: float = 0.0,
        max_runtime_seconds: Optional[float] = None,
        max_hold_seconds_without_new_high: float = 15 * 60,
        new_high_epsilon_pct: float = 0.0002,
        trend_exit_enabled: bool = True,
        trend_interval: str = Client.KLINE_INTERVAL_1MINUTE,
        trend_limit: int = 60,
        trend_sma_period: int = 25,
        on_sell: Optional[Callable[[str, dict, dict], Any]] = None,
    ) -> dict | None:
        symbol = symbol.upper()
        base_asset = self._get_base_asset(symbol)

        start_ts = time.time()
        max_price = self.get_price(symbol)
        last_new_high_ts = start_ts

        logger.info(
            f"TRAILING START | {symbol} | trailing={trailing_pct*100:.2f}% | "
            f"start_price={max_price:.2f} | poll={poll_seconds:.1f}s | "
            f"time_stop={max_hold_seconds_without_new_high:.0f}s | trend_exit={trend_exit_enabled}"
        )

        while True:
            now = time.time()

            if max_runtime_seconds is not None and (now - start_ts) >= max_runtime_seconds:
                logger.warning("TRAILING STOP ended by max_runtime_seconds")
                return None

            qty = self.get_asset_free(base_asset)
            if qty <= 0.0:
                logger.warning(f"TRAILING STOP ended: no {base_asset} free balance")
                return None

            current = self.get_price(symbol)

            # Update max price
            if current > max_price * (1 + new_high_epsilon_pct):
                max_price = current
                last_new_high_ts = now
                logger.info(f"NEW HIGH | {symbol} | max_price={max_price:.2f}")

            if (now - start_ts) < min_hold_seconds:
                time.sleep(poll_seconds)
                continue

            # 1) TIME STOP — purely time-based
            if (now - last_new_high_ts) >= max_hold_seconds_without_new_high:
                logger.warning(
                    f"TIME STOP TRIGGER | {symbol} | "
                    f"no_new_high_for={(now - last_new_high_ts):.0f}s | "
                    f"current={current:.2f} | max={max_price:.2f}"
                )

                try:
                    self._require_tradeable_qty(
                        symbol,
                        qty,
                        context="Time stop sell",
                        ignore_min_trade=True
                    )
                except ValueError as e:
                    logger.warning(str(e))
                    time.sleep(poll_seconds)
                    continue

                order = self.safe_sell_all(symbol)
                if order:
                    self._emit_sell(
                        "TIME_STOP",
                        order,
                        {
                            "current": current,
                            "max_price": max_price,
                            "no_new_high_for_s": now - last_new_high_ts,
                        },
                        on_sell=on_sell,
                    )
                    return order

            # 2) TREND EXIT — SMA based
            if trend_exit_enabled:
                try:
                    sma_slow = self.get_sma(
                        symbol=symbol,
                        interval=trend_interval,
                        limit=trend_limit,
                        period=trend_sma_period,
                    )

                    if current < sma_slow:
                        logger.warning(
                            f"TREND EXIT TRIGGER | {symbol} | "
                            f"current={current:.2f} < SMA{trend_sma_period}={sma_slow:.2f}"
                        )

                        try:
                            self._require_tradeable_qty(
                                symbol,
                                qty,
                                context="Trend exit sell",
                                ignore_min_trade=True
                            )
                        except ValueError as e:
                            logger.warning(str(e))
                            time.sleep(poll_seconds)
                            continue

                        order = self.safe_sell_all(symbol)
                        if order:
                            self._emit_sell(
                                "TREND_EXIT",
                                order,
                                {
                                    "current": current,
                                    "sma": sma_slow,
                                    "max_price": max_price,
                                },
                                on_sell=on_sell,
                            )
                            return order
                except Exception as e:
                    logger.warning(f"TREND EXIT skipped (calc error): {e}")

            # 3) TRAILING STOP — price based
            stop_price = max_price * (1 - trailing_pct)
            if current <= stop_price:
                drop_pct = (max_price - current) / max_price
                logger.warning(
                    f"TRAILING TRIGGER | {symbol} | "
                    f"current={current:.2f} | stop={stop_price:.2f} | "
                    f"drop={drop_pct*100:.2f}%"
                )

                try:
                    self._require_tradeable_qty(
                        symbol,
                        qty,
                        context="Trailing sell",
                        ignore_min_trade=True
                    )
                except ValueError as e:
                    logger.warning(str(e))
                    time.sleep(poll_seconds)
                    continue

                order = self.safe_sell_all(symbol)
                if order:
                    self._emit_sell(
                        "TRAILING",
                        order,
                        {
                            "current": current,
                            "max_price": max_price,
                            "stop_price": stop_price,
                            "drop_pct": drop_pct,
                        },
                        on_sell=on_sell,
                    )
                    return order

            time.sleep(poll_seconds)


    def _sma(self, values: list[float], period: int) -> float:
        if len(values) < period:
            raise ValueError("Not enough data for SMA")
        return sum(values[-period:]) / period

    def get_sma(self, symbol: str, interval: str, limit: int, period: int) -> float:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=limit)
        closes = [float(k[4]) for k in klines]
        return self._sma(closes, period)
    

