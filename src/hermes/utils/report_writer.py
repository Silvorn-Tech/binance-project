# hermes/utils/report_manager.py
from pathlib import Path
from datetime import datetime
import csv

from hermes.service.bot_state import BotRuntimeState


class ReportManager:
    def __init__(self, base_dir: str = "reports"):
        self.base_dir = Path(base_dir)
        self.daily_path = self.base_dir / "daily"
        self.trades_path = self.base_dir / "trades"
        self.bots_path = self.base_dir / "bots"

        self.daily_path.mkdir(parents=True, exist_ok=True)
        self.trades_path.mkdir(parents=True, exist_ok=True)
        self.bots_path.mkdir(parents=True, exist_ok=True)

    def get_daily_csv(self, date: str) -> Path:
        return self.daily_path / f"{date}_daily.csv"

    def get_trades_csv(self, date: str) -> Path:
        return self.trades_path / f"{date}_trades.csv"

    def get_bot_report_csv(self, symbol: str, timestamp: str) -> Path:
        return self.bots_path / f"{symbol}_{timestamp}.csv"


def write_bot_report(state: BotRuntimeState) -> str:
    manager = ReportManager()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_path = manager.get_bot_report_csv(state.symbol, timestamp)

    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["field", "value"])

        writer.writerow(["symbol", state.symbol])
        writer.writerow(["profile", state.profile])
        writer.writerow(["running", state.running])
        writer.writerow(["total_pnl_usdt", f"{state.total_pnl_usdt:.4f}"])
        writer.writerow(["last_trade_profit_usdt", f"{state.last_trade_profit_usdt:.4f}"])
        writer.writerow(["buys_today", state.buys_today])
        writer.writerow(["spent_today", f"{state.spent_today:.2f}"])
        writer.writerow(["last_action", state.last_action])
        writer.writerow(["entry_price", state.entry_price])
        writer.writerow(["arm_price", state.arm_price])
        writer.writerow(["last_price", state.last_price])
        writer.writerow(["updated_at", state.last_update])

    return str(file_path)
