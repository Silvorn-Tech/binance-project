from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from hermes.persistence.db import SessionLocal
from hermes.persistence.models import PerformanceWindow, RealTrade
from hermes.repository.performance_repository import PerformanceRepository


@dataclass(frozen=True)
class _WindowKey:
    profile_id: int
    asset_id: int
    window_start: datetime
    window_end: datetime


def run_performance_window_job(*, window_minutes: int = 60) -> int:
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")

    window_seconds = window_minutes * 60

    def _window_start(ts: datetime) -> datetime:
        epoch = int(ts.timestamp())
        start_epoch = epoch - (epoch % window_seconds)
        return datetime.fromtimestamp(start_epoch, tz=timezone.utc)

    with SessionLocal() as session:
        trades = (
            session.execute(select(RealTrade).order_by(RealTrade.exit_time.asc()))
            .scalars()
            .all()
        )

        if not trades:
            return 0

        buckets: dict[_WindowKey, list[RealTrade]] = defaultdict(list)
        for trade in trades:
            ts = trade.exit_time
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            start = _window_start(ts)
            end = start + timedelta(seconds=window_seconds)
            key = _WindowKey(
                profile_id=trade.profile_id,
                asset_id=trade.asset_id,
                window_start=start,
                window_end=end,
            )
            buckets[key].append(trade)

        repo = PerformanceRepository(session)
        upserted = 0

        for key, bucket in buckets.items():
            pnls = [float(t.pnl) for t in bucket]
            trades_count = len(pnls)
            wins = sum(1 for pnl in pnls if pnl > 0)
            win_rate = wins / trades_count if trades_count else 0.0
            avg_pnl = sum(pnls) / trades_count if trades_count else 0.0

            cumulative = []
            running = 0.0
            for pnl in pnls:
                running += pnl
                cumulative.append(running)

            if trades_count > 1:
                pnl_slope = (cumulative[-1] - cumulative[0]) / (trades_count - 1)
            else:
                pnl_slope = 0.0

            max_drawdown = _max_drawdown(cumulative)

            window = PerformanceWindow(
                profile_id=key.profile_id,
                asset_id=key.asset_id,
                window_start=key.window_start,
                window_end=key.window_end,
                trades_count=trades_count,
                win_rate=win_rate,
                avg_pnl=avg_pnl,
                pnl_slope=pnl_slope,
                max_drawdown=max_drawdown,
            )

            repo.upsert_window(window)
            upserted += 1

        session.commit()
        logger.info("Performance windows upserted: %d", upserted)
        return upserted


def _max_drawdown(cumulative: list[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for value in cumulative:
        if value > peak:
            peak = value
        if peak <= 0:
            continue
        drawdown = peak - value
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return (max_drawdown / peak) if peak > 0 else 0.0
