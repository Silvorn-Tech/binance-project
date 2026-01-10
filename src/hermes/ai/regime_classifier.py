from __future__ import annotations

from hermes.ai.types import MarketRegime
from hermes.persistence.models import PerformanceWindow
from hermes.repository.performance_repository import PerformanceRepository


class RegimeClassifier:
    def __init__(self, repo: PerformanceRepository) -> None:
        self.repo = repo

    def classify(self, profile_id: int, asset_id: int) -> MarketRegime:
        window = self.repo.get_latest_window(profile_id, asset_id)
        return self.classify_window(window)

    def classify_window(self, window: PerformanceWindow | None) -> MarketRegime:
        if window is None:
            return MarketRegime.NO_EDGE

        if window.trades_count > 20 and window.avg_pnl < 0:
            return MarketRegime.NO_EDGE

        if window.trades_count > 10 and window.win_rate < 0.5:
            return MarketRegime.CHOPPY

        return MarketRegime.TREND_FRIENDLY
