import os
import time
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger
from binance.client import Client

from utils.logging_config import setup_logging
from .providers.binance import Binance

load_dotenv()
setup_logging()

# =========================
# BOT CONFIG
# =========================
SYMBOL = "BTCUSDT"
BASE_ASSET = "BTC"

BUY_USDT = 7.0
TRAILING_PCT = 0.01  # 1%

POLL_SECONDS_IDLE = 10.0
POLL_SECONDS_IN_POS = 3.0
ERROR_BACKOFF_SECONDS = 5.0
COOLDOWN_AFTER_SELL_SECONDS = 60.0

# =========================
# ENTRY SIGNAL CONFIG (SMA)
# =========================
KLINE_INTERVAL = Client.KLINE_INTERVAL_1MINUTE
KLINE_LIMIT = 60
SMA_FAST = 7
SMA_SLOW = 25

# =========================
# RISK LIMITS (ANTI-BLOWUP)
# =========================
MAX_BUYS_PER_DAY = 1
DAILY_BUDGET_USDT = 20.0


def sleep_s(seconds: float) -> None:
    time.sleep(seconds)


def day_key_bogota() -> str:
    # Uses your machine time (Bogotá is your timezone generally).
    return datetime.now().strftime("%Y-%m-%d")


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("Not enough data for SMA")
    return sum(values[-period:]) / period


def entry_signal_sma(binance: Binance, symbol: str) -> bool:
    klines = binance.get_klines(symbol=symbol, interval=KLINE_INTERVAL, limit=KLINE_LIMIT)
    closes = [float(k[4]) for k in klines]

    closes_now = closes[:-1]
    closes_prev = closes[:-2]

    sma_fast_now = sma(closes_now, SMA_FAST)
    sma_slow_now = sma(closes_now, SMA_SLOW)

    sma_fast_prev = sma(closes_prev, SMA_FAST)
    sma_slow_prev = sma(closes_prev, SMA_SLOW)

    crossed_up = (sma_fast_prev <= sma_slow_prev) and (sma_fast_now > sma_slow_now)
    return crossed_up



def main() -> None:
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        logger.error("Missing BINANCE_API_KEY or BINANCE_API_SECRET")
        raise ValueError("API key and secret must be set in environment variables.")

    binance = Binance(api_key, api_secret)

    logger.info("BOT STARTED | cyclic mode | trailing stop + SMA entry")

    day = day_key_bogota()
    buys_today = 0
    spent_today = 0.0

    while True:
        try:
            # Reset counters daily
            new_day = day_key_bogota()
            if new_day != day:
                day = new_day
                buys_today = 0
                spent_today = 0.0
                logger.info(f"DAILY RESET | day={day}")

            usdt = binance.get_asset_free("USDT")
            bnb = binance.get_asset_free("BNB")
            base_qty = binance.get_asset_free(BASE_ASSET)

            logger.info(
                f"BALANCES | USDT={usdt:.8f} | BNB={bnb:.8f} | {BASE_ASSET}={base_qty:.8f} | "
                f"buys_today={buys_today}/{MAX_BUYS_PER_DAY} | spent_today={spent_today:.2f}/{DAILY_BUDGET_USDT:.2f}"
            )

            TRAILING_KWARGS = dict(
                symbol=SYMBOL,
                trailing_pct=TRAILING_PCT,
                poll_seconds=POLL_SECONDS_IN_POS,
                min_hold_seconds=0.0,
                max_runtime_seconds=None,
                max_hold_seconds_without_new_high=5 * 60,
                trend_exit_enabled=True,
                trend_sma_period=25,
            )

            # 1) If we already have a position -> manage with trailing stop
            if base_qty > 0.0:
                ok, _ = binance.can_trade(SYMBOL, base_qty)
                if ok:
                    result = binance.trailing_stop_sell_all_pct(**TRAILING_KWARGS)

                    if result is not None:
                        logger.info(f"COOLDOWN | Waiting {COOLDOWN_AFTER_SELL_SECONDS:.0f}s...")
                        sleep_s(COOLDOWN_AFTER_SELL_SECONDS)
                else:
                    logger.warning("DUST POSITION | balance too small to sell. Ignoring position.")
                    sleep_s(POLL_SECONDS_IDLE)

                continue


            # 2) No position -> risk checks before buying
            if buys_today >= MAX_BUYS_PER_DAY:
                logger.warning("RISK LIMIT | MAX_BUYS_PER_DAY reached. Going idle...")
                sleep_s(POLL_SECONDS_IDLE)
                continue

            if spent_today + BUY_USDT > DAILY_BUDGET_USDT:
                logger.warning("RISK LIMIT | DAILY_BUDGET_USDT reached. Going idle...")
                sleep_s(POLL_SECONDS_IDLE)
                continue

            # 3) Entry decision
            if usdt >= BUY_USDT:
                if entry_signal_sma(binance, SYMBOL):
                    logger.info(f"ENTRY SIGNAL OK | Buying {SYMBOL} with {BUY_USDT} USDT")
                    order = binance.buy(SYMBOL, BUY_USDT)
                    logger.success(f"BUY FILLED | orderId={order.get('orderId')}")

                    buys_today += 1
                    spent_today += BUY_USDT

                    # Immediately manage with trailing stop
                    result = binance.trailing_stop_sell_all_pct(**TRAILING_KWARGS)

                    if result is not None:
                        logger.info(f"COOLDOWN | Waiting {COOLDOWN_AFTER_SELL_SECONDS:.0f}s...")
                        sleep_s(COOLDOWN_AFTER_SELL_SECONDS)

                else:
                    logger.info("NO SIGNAL | No SMA cross up. Waiting...")
                    sleep_s(POLL_SECONDS_IDLE)

            else:
                logger.info(f"IDLE | Not enough USDT to buy (need {BUY_USDT}). Sleeping...")
                sleep_s(POLL_SECONDS_IDLE)

        except KeyboardInterrupt:
            logger.warning("BOT STOPPED by user (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.exception(f"BOT ERROR (recovering): {e}")
            sleep_s(ERROR_BACKOFF_SECONDS)


if __name__ == "__main__":
    main()
