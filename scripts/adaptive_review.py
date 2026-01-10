#!/usr/bin/env python3
import argparse
from typing import Iterable

from hermes.reporting.trade_reporter import TradeReporter
from hermes.utils.adaptive_controller import AdaptiveController


def _print_trades(trades: Iterable[dict]) -> None:
    for idx, trade in enumerate(trades, start=1):
        pnl = trade.get("trade_pnl", "0.0")
        spent = trade.get("usdt_spent", "0.0")
        ts = trade.get("timestamp", "")
        side = trade.get("side", "")
        symbol = trade.get("symbol", "")
        print(f"{idx:>2}. {ts} | {symbol} | {side} | pnl={pnl} | spent={spent}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quick adaptive heuristic review for recent trades."
    )
    parser.add_argument("--bot-id", required=True, help="Bot ID to filter trades.")
    parser.add_argument("--limit", type=int, default=10, help="Number of trades to load.")
    parser.add_argument("--side", default="SELL", help="Trade side to filter (default: SELL).")
    args = parser.parse_args()

    reporter = TradeReporter()
    trades = reporter.get_recent_trades(
        bot_id=args.bot_id,
        limit=args.limit,
        side=args.side,
    )

    if not trades:
        print("No trades found for the given bot_id/side.")
        return 1

    controller = AdaptiveController(reporter)
    metrics = controller.compute_metrics(trades)

    print("Last trades:")
    _print_trades(trades)
    print("")
    print("Metrics:")
    print(f"- total_trades: {metrics.total_trades}")
    print(f"- win_rate: {metrics.win_rate:.2f}")
    print(f"- cumulative_pnl: {metrics.cumulative_pnl:.4f}")
    print(f"- avg_abs_pnl: {metrics.avg_abs_pnl:.4f}")
    print(f"- pnl_volatility: {metrics.pnl_volatility:.4f}")
    print(f"- avg_abs_pnl_pct: {metrics.avg_abs_pnl_pct}")
    print(f"- pnl_volatility_pct: {metrics.pnl_volatility_pct}")
    print(f"- drawdown_pct: {metrics.drawdown_pct:.4f}")
    print(f"- negative_streak: {metrics.negative_streak}")
    print(f"- wins_last_3: {metrics.wins_last_3}")
    print(f"- flip_rate: {metrics.flip_rate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
