from pathlib import Path
from datetime import datetime
import csv

from hermes.service.bot_state import BotRuntimeState


def write_bot_report(state: BotRuntimeState) -> str:
    reports_dir = Path("reports") / "bots"
    reports_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_path = reports_dir / f"{state.symbol}_{date}.csv"

    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["field", "value"])

        writer.writerow(["symbol", state.symbol])
        writer.writerow(["profile", state.profile])
        writer.writerow(["running", state.running])
        writer.writerow(["total_pnl_usdt", f"{state.total_pnl_usdt:.4f}"])
        writer.writerow(["buys_today", state.buys_today])
        writer.writerow(["spent_today", f"{state.spent_today:.2f}"])
        writer.writerow(["last_action", state.last_action])
        writer.writerow(["entry_price", state.entry_price])
        writer.writerow(["arm_price", state.arm_price])
        writer.writerow(["last_price", state.last_price])

    return str(file_path)
