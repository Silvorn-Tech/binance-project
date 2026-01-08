from pathlib import Path

def get_daily_csv(self, date: str) -> Path:
    return self.daily_path / f"{date}_daily.csv"

def get_trades_csv(self, date: str) -> Path:
    return self.trades_path / f"{date}_trades.csv"
