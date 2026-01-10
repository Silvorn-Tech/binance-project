from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hermes.reporting.trade_reporter import TradeReporter
from hermes.utils.adaptive_controller import AdaptiveController


@dataclass(frozen=True)
class PostMortemSummary:
    text: str
    path: Path | None = None


class PostMortemAuditor:
    def __init__(
        self,
        reporter: TradeReporter,
        controller: AdaptiveController | None = None,
    ) -> None:
        self.reporter = reporter
        self.controller = controller or AdaptiveController(reporter)

    def generate_summary(self, bot_id: str, limit: int = 30) -> str:
        trades = self.reporter.get_recent_trades(
            bot_id=bot_id,
            limit=limit,
            side="SELL",
        )
        if not trades:
            return "Post-mortem: no trades found."

        metrics = self.controller.compute_metrics(trades)
        losses = []
        for trade in trades:
            try:
                pnl = float(trade.get("trade_pnl", 0.0))
            except (TypeError, ValueError):
                pnl = 0.0
            if pnl < 0:
                losses.append(pnl)

        lines: list[str] = []
        lines.append("POST-MORTEM")
        lines.append("")
        lines.append("POR QUE PERDI")
        if metrics.cumulative_pnl >= 0:
            lines.append("- no hay perdida neta en la ventana reciente")
        else:
            lines.append(f"- cumulative_pnl={metrics.cumulative_pnl:+.4f}")
            lines.append(f"- win_rate={metrics.win_rate:.2f}")
            lines.append(f"- loss_streak={metrics.negative_streak}")
            lines.append(f"- drawdown={self._fmt_pct(metrics.drawdown_pct)}")
            if metrics.flip_rate is not None:
                lines.append(f"- flip_rate={metrics.flip_rate:.2f}")
            if metrics.avg_abs_pnl_pct is not None:
                lines.append(f"- avg_abs_pnl_pct={self._fmt_pct(metrics.avg_abs_pnl_pct)}")
            if losses:
                lines.append(f"- worst_loss={min(losses):+.4f}")

        lines.append("")
        lines.append("POR QUE REDUJE RIESGO")
        event = self._latest_risk_event(bot_id)
        if event is None:
            lines.append("- no hay eventos adaptivos registrados")
        else:
            reason = event.get("reason") or "unknown"
            lines.append(f"- state={event.get('adaptive_state')} reason={reason}")
            if event.get("previous_state"):
                lines.append(f"- previous_state={event.get('previous_state')}")
            if event.get("drawdown_pct"):
                lines.append(f"- drawdown={self._fmt_pct(self._parse_float(event.get('drawdown_pct')))}")
            if event.get("negative_streak"):
                lines.append(f"- loss_streak={event.get('negative_streak')}")
            if event.get("flip_rate"):
                lines.append(f"- flip_rate={event.get('flip_rate')}")

        return "\n".join(lines)

    def write_latest_summary(self, bot_id: str, limit: int = 30) -> PostMortemSummary:
        text = self.generate_summary(bot_id=bot_id, limit=limit)
        path = Path("reports/post_mortem")
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"{bot_id}_latest.txt"
        file_path.write_text(text)
        return PostMortemSummary(text=text, path=file_path)

    def _latest_risk_event(self, bot_id: str) -> dict | None:
        events = self.reporter.get_recent_adaptive_events(bot_id=bot_id, limit=25)
        for event in reversed(events):
            state = event.get("adaptive_state")
            if state in {"DEFENSIVE", "COOLDOWN_EXTENDED", "SLEEP"}:
                return event
        return None

    def _fmt_pct(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value * 100:.2f}%"

    def _parse_float(self, raw) -> float | None:
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
