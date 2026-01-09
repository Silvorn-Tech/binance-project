import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class TradeReporter:
    def __init__(self, file_path: str | Path = "reports/trades/trades.csv") -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._cumulative_pnl = self._load_last_cumulative()

    def _load_last_cumulative(self) -> float:
        if not self.file_path.exists():
            return 0.0

        try:
            with self.file_path.open("r", newline="") as f:
                rows = list(csv.DictReader(f))
                if not rows:
                    return 0.0
                return float(rows[-1].get("cumulative_pnl", 0.0))
        except Exception:
            return 0.0

    def record_trade(
        self,
        *,
        bot_id: str,
        profile: str,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        usdt_spent: float,
        usdt_received: float,
        trade_pnl: float,
    ) -> None:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        self._cumulative_pnl += trade_pnl

        row = {
            "timestamp": now.isoformat(),
            "date": date_str,
            "bot_id": bot_id,
            "profile": profile,
            "symbol": symbol,
            "side": side,
            "price": f"{price:.8f}",
            "qty": f"{qty:.8f}",
            "usdt_spent": f"{usdt_spent:.8f}",
            "usdt_received": f"{usdt_received:.8f}",
            "trade_pnl": f"{trade_pnl:.8f}",
            "cumulative_pnl": f"{self._cumulative_pnl:.8f}",
        }

        file_exists = self.file_path.exists()
        with self.file_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
