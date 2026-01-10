from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from hermes.persistence.models import PerformanceWindow


class PerformanceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_latest_window(self, profile_id: int, asset_id: int) -> PerformanceWindow | None:
        stmt = (
            select(PerformanceWindow)
            .where(
                PerformanceWindow.profile_id == profile_id,
                PerformanceWindow.asset_id == asset_id,
            )
            .order_by(PerformanceWindow.window_end.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def upsert_window(self, window: PerformanceWindow) -> PerformanceWindow:
        stmt = (
            select(PerformanceWindow)
            .where(
                PerformanceWindow.profile_id == window.profile_id,
                PerformanceWindow.asset_id == window.asset_id,
                PerformanceWindow.window_start == window.window_start,
                PerformanceWindow.window_end == window.window_end,
            )
            .limit(1)
        )
        existing = self.session.execute(stmt).scalars().first()
        if existing is None:
            self.session.add(window)
            return window

        existing.trades_count = window.trades_count
        existing.win_rate = window.win_rate
        existing.avg_pnl = window.avg_pnl
        existing.pnl_slope = window.pnl_slope
        existing.max_drawdown = window.max_drawdown
        return existing
