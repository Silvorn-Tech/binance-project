from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketFeatures:
    win_rate: float
    avg_pnl: float
    trades_count: int
