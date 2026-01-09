from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from hermes.reporting.trade_reporter import TradeReporter

if TYPE_CHECKING:
    from hermes.utils.bot import Bot


@dataclass(frozen=True)
class AdaptiveMetrics:
    total_trades: int
    win_rate: float
    cumulative_pnl: float
    drawdown_pct: float
    negative_streak: int
    wins_last_3: int


class AdaptiveController:
    def __init__(
        self,
        reporter: TradeReporter,
        window_size: int = 10,
        min_trades: int = 5,
    ) -> None:
        self.reporter = reporter
        self.window_size = window_size
        self.min_trades = min_trades

        self._defensive_drawdown_pct = 0.05
        self._defensive_win_rate = 0.40
        self._defensive_negative_streak = 3
        self._recovery_wins_last_3 = 2

    def evaluate(self, bot: Bot) -> None:
        trades = self.reporter.get_recent_trades(
            bot_id=bot.config.bot_id,
            limit=self.window_size,
            side="SELL",
        )
        metrics = self._compute_metrics(trades)

        logger.info(
            "[ADAPTIVE] Bot=%s | profile=%s | win_rate=%.2f | pnl=%.4f | drawdown=%.2f | neg_streak=%d | state=%s",
            bot.config.bot_id,
            bot.config.profile,
            metrics.win_rate,
            metrics.cumulative_pnl,
            metrics.drawdown_pct,
            metrics.negative_streak,
            bot.state.adaptive_state,
        )

        if bot.config.profile != "equilibrium":
            return

        if metrics.total_trades < self.min_trades:
            return

        target_state = bot.state.adaptive_state
        reason = None

        if bot.state.adaptive_state == "DEFENSIVE":
            if metrics.wins_last_3 >= self._recovery_wins_last_3:
                target_state = "NORMAL"
                reason = "recovery"
        else:
            if (
                metrics.negative_streak >= self._defensive_negative_streak
                or metrics.drawdown_pct >= self._defensive_drawdown_pct
                or metrics.win_rate <= self._defensive_win_rate
            ):
                target_state = "DEFENSIVE"
                reason = "performance_drop"

        if target_state != bot.state.adaptive_state:
            bot.apply_adaptive_state(target_state, reason=reason)

    def _compute_metrics(self, trades: list[dict]) -> AdaptiveMetrics:
        pnls: list[float] = []
        for row in trades:
            try:
                pnls.append(float(row.get("trade_pnl", 0.0)))
            except (TypeError, ValueError):
                pnls.append(0.0)

        total = len(pnls)
        wins = sum(1 for pnl in pnls if pnl > 0)
        win_rate = (wins / total) if total else 0.0

        cumulative = sum(pnls)
        drawdown_pct = self._max_drawdown_pct(pnls)
        negative_streak = self._negative_streak(pnls)

        wins_last_3 = 0
        for pnl in pnls[-3:]:
            if pnl > 0:
                wins_last_3 += 1

        return AdaptiveMetrics(
            total_trades=total,
            win_rate=win_rate,
            cumulative_pnl=cumulative,
            drawdown_pct=drawdown_pct,
            negative_streak=negative_streak,
            wins_last_3=wins_last_3,
        )

    def _max_drawdown_pct(self, pnls: list[float]) -> float:
        peak = 0.0
        running = 0.0
        max_drawdown = 0.0

        for pnl in pnls:
            running += pnl
            if running > peak:
                peak = running
            if peak <= 0:
                continue
            drawdown = peak - running
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return (max_drawdown / peak) if peak > 0 else 0.0

    def _negative_streak(self, pnls: list[float]) -> int:
        streak = 0
        for pnl in reversed(pnls):
            if pnl < 0:
                streak += 1
            else:
                break
        return streak
