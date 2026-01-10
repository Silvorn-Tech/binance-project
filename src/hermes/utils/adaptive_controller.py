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
    avg_abs_pnl: float
    pnl_volatility: float
    avg_abs_pnl_pct: float | None
    pnl_volatility_pct: float | None
    drawdown_pct: float
    negative_streak: int
    wins_last_3: int
    flip_rate: float | None


class AdaptiveController:
    _STATE_PRIORITY = ("COOLDOWN_EXTENDED", "DEFENSIVE", "SLEEP", "NORMAL")

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
        self._defensive_volatility_pct = 0.02
        self._defensive_range_avg_abs_pnl_pct = 0.002
        self._defensive_lateral_flip_rate = 0.7
        self._defensive_lateral_avg_abs_pnl_pct = 0.002
        self._sleep_volatility_pct = 0.0015
        self._sleep_avg_abs_pnl_pct = 0.001
        self._sleep_flip_rate = 0.3
        self._cooldown_extended_negative_streak = 5
        self._cooldown_extended_drawdown_pct = 0.08
        self._cooldown_extended_win_rate = 0.30

    def compute_metrics(self, trades: list[dict]) -> AdaptiveMetrics:
        return self._compute_metrics(trades)

    def decide_target_state(
        self,
        metrics: AdaptiveMetrics,
        current_state: str = "NORMAL",
    ) -> tuple[str, str | None]:
        return self._decide_target_state(metrics=metrics, current_state=current_state)

    def evaluate(self, bot: Bot) -> None:
        trades = self.reporter.get_recent_trades(
            bot_id=bot.config.bot_id,
            limit=self.window_size,
            side="SELL",
        )
        metrics = self._compute_metrics(trades)

        logger.info(
            "[ADAPTIVE] Bot=%s | profile=%s | win_rate=%.2f | pnl=%.4f | drawdown=%.2f | "
            "neg_streak=%d | avg_abs=%.4f | vol=%.4f | flip_rate=%s | state=%s",
            bot.config.bot_id,
            bot.config.profile,
            metrics.win_rate,
            metrics.cumulative_pnl,
            metrics.drawdown_pct,
            metrics.negative_streak,
            metrics.avg_abs_pnl,
            metrics.pnl_volatility,
            f"{metrics.flip_rate:.2f}" if metrics.flip_rate is not None else "—",
            bot.state.adaptive_state,
        )

        if bot.config.profile != "equilibrium":
            return

        if metrics.total_trades < self.min_trades:
            return

        target_state, reason = self._decide_target_state(
            metrics=metrics,
            current_state=bot.state.adaptive_state,
        )

        if target_state != bot.state.adaptive_state:
            if self.reporter is not None:
                self.reporter.record_adaptive_event(
                    bot_id=bot.config.bot_id,
                    profile=bot.config.profile,
                    symbol=bot.config.symbol,
                    previous_state=bot.state.adaptive_state,
                    adaptive_state=target_state,
                    reason=reason,
                    metrics={
                        "win_rate": metrics.win_rate,
                        "cumulative_pnl": metrics.cumulative_pnl,
                        "drawdown_pct": metrics.drawdown_pct,
                        "negative_streak": metrics.negative_streak,
                        "avg_abs_pnl_pct": metrics.avg_abs_pnl_pct,
                        "pnl_volatility_pct": metrics.pnl_volatility_pct,
                        "flip_rate": metrics.flip_rate,
                    },
                )
            bot.apply_adaptive_state(target_state, reason=reason)

    def _compute_metrics(self, trades: list[dict]) -> AdaptiveMetrics:
        pnls: list[float] = []
        pnl_pcts: list[float] = []
        signs: list[int] = []
        for row in trades:
            try:
                pnl = float(row.get("trade_pnl", 0.0))
            except (TypeError, ValueError):
                pnl = 0.0
            pnls.append(pnl)
            if pnl > 0:
                signs.append(1)
            elif pnl < 0:
                signs.append(-1)

            spent_raw = row.get("usdt_spent")
            try:
                spent = float(spent_raw) if spent_raw is not None else None
            except (TypeError, ValueError):
                spent = None
            if spent is not None and spent > 0:
                pnl_pcts.append(pnl / spent)

        total = len(pnls)
        wins = sum(1 for pnl in pnls if pnl > 0)
        win_rate = (wins / total) if total else 0.0

        cumulative = sum(pnls)
        avg_abs_pnl = (sum(abs(pnl) for pnl in pnls) / total) if total else 0.0
        pnl_volatility = self._stddev(pnls)
        avg_abs_pnl_pct = (
            (sum(abs(pnl) for pnl in pnl_pcts) / len(pnl_pcts)) if pnl_pcts else None
        )
        pnl_volatility_pct = self._stddev(pnl_pcts) if pnl_pcts else None
        drawdown_pct = self._max_drawdown_pct(pnls)
        negative_streak = self._negative_streak(pnls)
        flip_rate = self._flip_rate(signs)

        wins_last_3 = 0
        for pnl in pnls[-3:]:
            if pnl > 0:
                wins_last_3 += 1

        return AdaptiveMetrics(
            total_trades=total,
            win_rate=win_rate,
            cumulative_pnl=cumulative,
            avg_abs_pnl=avg_abs_pnl,
            pnl_volatility=pnl_volatility,
            avg_abs_pnl_pct=avg_abs_pnl_pct,
            pnl_volatility_pct=pnl_volatility_pct,
            drawdown_pct=drawdown_pct,
            negative_streak=negative_streak,
            wins_last_3=wins_last_3,
            flip_rate=flip_rate,
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

    def _stddev(self, values: list[float]) -> float:
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        var = sum((val - mean) ** 2 for val in values) / len(values)
        return var ** 0.5

    def _flip_rate(self, signs: list[int]) -> float | None:
        if len(signs) < 2:
            return None
        flips = 0
        last = signs[0]
        for current in signs[1:]:
            if current != last:
                flips += 1
                last = current
        return flips / (len(signs) - 1)

    def _fmt_pct(self, value: float | None) -> str:
        if value is None:
            return "—"
        return f"{value * 100:.2f}%"

    def _fmt_reason(self, label: str, detail: str | None = None) -> str:
        if detail:
            return f"{label} ({detail})"
        return label

    def _decide_target_state(
        self,
        *,
        metrics: AdaptiveMetrics,
        current_state: str,
    ) -> tuple[str, str | None]:
        if current_state == "SLEEP":
            if self._should_wake(metrics):
                return (
                    "NORMAL",
                    self._fmt_reason(
                        "market_active",
                        f"vol={self._fmt_pct(metrics.pnl_volatility_pct)}",
                    ),
                )
            return "SLEEP", None

        if current_state == "COOLDOWN_EXTENDED":
            if metrics.wins_last_3 >= self._recovery_wins_last_3:
                return (
                    "DEFENSIVE",
                    self._fmt_reason(
                        "recovered",
                        f"{metrics.wins_last_3}/3 wins",
                    ),
                )
            return "COOLDOWN_EXTENDED", None

        if current_state == "DEFENSIVE":
            if metrics.wins_last_3 >= self._recovery_wins_last_3:
                return (
                    "NORMAL",
                    self._fmt_reason(
                        "recovered",
                        f"{metrics.wins_last_3}/3 wins",
                    ),
                )
            return "DEFENSIVE", None

        candidates: dict[str, str] = {}
        cooldown_reason = self._cooldown_extended_reason(metrics)
        if cooldown_reason is not None:
            candidates["COOLDOWN_EXTENDED"] = cooldown_reason

        defensive_reason = self._defensive_reason(metrics)
        if defensive_reason is not None:
            candidates["DEFENSIVE"] = defensive_reason

        sleep_reason = self._sleep_reason(metrics)
        if sleep_reason is not None:
            candidates["SLEEP"] = sleep_reason

        for state in self._STATE_PRIORITY:
            if state in candidates:
                return state, candidates[state]

        return "NORMAL", None

    def _sleep_reason(self, metrics: AdaptiveMetrics) -> str | None:
        if (
            metrics.pnl_volatility_pct is not None
            and metrics.pnl_volatility_pct <= self._sleep_volatility_pct
            and metrics.avg_abs_pnl_pct is not None
            and metrics.avg_abs_pnl_pct <= self._sleep_avg_abs_pnl_pct
            and (
                metrics.flip_rate is None
                or metrics.flip_rate <= self._sleep_flip_rate
            )
        ):
            return self._fmt_reason(
                "market_dead",
                f"vol={self._fmt_pct(metrics.pnl_volatility_pct)}, avg_abs={self._fmt_pct(metrics.avg_abs_pnl_pct)}",
            )
        return None

    def _should_wake(self, metrics: AdaptiveMetrics) -> bool:
        if metrics.pnl_volatility_pct is not None and metrics.pnl_volatility_pct > self._sleep_volatility_pct:
            return True
        if metrics.avg_abs_pnl_pct is not None and metrics.avg_abs_pnl_pct > self._sleep_avg_abs_pnl_pct:
            return True
        return False

    def _cooldown_extended_reason(self, metrics: AdaptiveMetrics) -> str | None:
        if metrics.negative_streak >= self._cooldown_extended_negative_streak:
            return self._fmt_reason(
                "loss_streak",
                f"{metrics.negative_streak}",
            )
        if metrics.drawdown_pct >= self._cooldown_extended_drawdown_pct:
            return self._fmt_reason(
                "drawdown",
                self._fmt_pct(metrics.drawdown_pct),
            )
        if metrics.win_rate <= self._cooldown_extended_win_rate:
            return self._fmt_reason(
                "win_rate",
                f"{metrics.win_rate:.2f}",
            )
        return None

    def _defensive_reason(self, metrics: AdaptiveMetrics) -> str | None:
        if metrics.negative_streak >= self._defensive_negative_streak:
            return self._fmt_reason(
                "loss_streak",
                f"{metrics.negative_streak}",
            )
        if metrics.drawdown_pct >= self._defensive_drawdown_pct:
            return self._fmt_reason(
                "drawdown",
                self._fmt_pct(metrics.drawdown_pct),
            )
        if metrics.win_rate <= self._defensive_win_rate:
            return self._fmt_reason(
                "win_rate",
                f"{metrics.win_rate:.2f}",
            )

        if (
            metrics.pnl_volatility_pct is not None
            and metrics.pnl_volatility_pct >= self._defensive_volatility_pct
            and metrics.cumulative_pnl < 0
        ):
            return self._fmt_reason(
                "volatility",
                self._fmt_pct(metrics.pnl_volatility_pct),
            )
        if (
            metrics.flip_rate is not None
            and metrics.flip_rate >= self._defensive_lateral_flip_rate
            and metrics.avg_abs_pnl_pct is not None
            and metrics.avg_abs_pnl_pct <= self._defensive_lateral_avg_abs_pnl_pct
        ):
            return self._fmt_reason(
                "lateral_chop",
                f"flip_rate={metrics.flip_rate:.2f}",
            )
        if (
            metrics.avg_abs_pnl_pct is not None
            and metrics.avg_abs_pnl_pct <= self._defensive_range_avg_abs_pnl_pct
        ):
            return self._fmt_reason(
                "range_tight",
                f"avg_abs={self._fmt_pct(metrics.avg_abs_pnl_pct)}",
            )
        return None
