import csv
from datetime import datetime, timezone
from pathlib import Path
from collections import deque


class TradeReporter:
    def __init__(self, file_path: str | Path = "reports/trades/trades.csv") -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.adaptive_file_path = Path("reports/adaptive/adaptive_events.csv")
        self.adaptive_file_path.parent.mkdir(parents=True, exist_ok=True)
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

    def get_recent_trades(
        self,
        *,
        bot_id: str,
        limit: int = 10,
        side: str | None = None,
    ) -> list[dict]:
        if not self.file_path.exists():
            return []

        rows: deque[dict] = deque(maxlen=max(limit, 1))
        with self.file_path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("bot_id") != bot_id:
                    continue
                if side and row.get("side") != side:
                    continue
                rows.append(row)

        return list(rows)

    def get_trades_since(
        self,
        *,
        bot_id: str,
        since_ts: float,
        side: str | None = None,
    ) -> list[dict]:
        if not self.file_path.exists():
            return []

        results: list[dict] = []
        with self.file_path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("bot_id") != bot_id:
                    continue
                if side and row.get("side") != side:
                    continue
                ts_value = self._parse_timestamp(row.get("timestamp"))
                if ts_value is None or ts_value < since_ts:
                    continue
                results.append(row)

        return results

    def _parse_timestamp(self, value: str | None) -> float | None:
        if not value:
            return None
        try:
            cleaned = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    def get_last_trades(
        self,
        *,
        bot_id: str,
        limit: int = 5,
    ) -> list[dict]:
        return self.get_recent_trades(
            bot_id=bot_id,
            limit=limit,
            side=None,
        )

    def record_adaptive_event(
        self,
        *,
        bot_id: str,
        profile: str,
        symbol: str,
        previous_state: str | None,
        adaptive_state: str,
        reason: str | None,
        metrics: dict[str, float | int | None] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        metrics_fields = [
            "win_rate",
            "cumulative_pnl",
            "drawdown_pct",
            "negative_streak",
            "avg_abs_pnl_pct",
            "pnl_volatility_pct",
            "flip_rate",
        ]
        row = {
            "timestamp": now.isoformat(),
            "bot_id": bot_id,
            "profile": profile,
            "symbol": symbol,
            "previous_state": previous_state or "",
            "adaptive_state": adaptive_state,
            "reason": reason or "",
        }
        metrics = metrics or {}
        for key in metrics_fields:
            value = metrics.get(key)
            row[key] = "" if value is None else str(value)

        file_exists = self.adaptive_file_path.exists()
        with self.adaptive_file_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def get_recent_adaptive_events(
        self,
        *,
        bot_id: str,
        limit: int = 10,
    ) -> list[dict]:
        if not self.adaptive_file_path.exists():
            return []

        rows: deque[dict] = deque(maxlen=max(limit, 1))
        with self.adaptive_file_path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("bot_id") != bot_id:
                    continue
                rows.append(row)

        return list(rows)
