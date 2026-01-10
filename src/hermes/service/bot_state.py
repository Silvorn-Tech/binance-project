# hermes/service/bot_state.py
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from hermes.utils.bot_config import BotConfig
from hermes.utils.trading_mode import TradingMode

@dataclass
class BotRuntimeState:
    # Identity
    bot_id: str
    symbol: str
    profile: str
    config: Optional[BotConfig] = None
    base_asset: Optional[str] = None
    trading_mode: TradingMode = TradingMode.SIMULATION
    live_authorized: bool = False
    live_authorized_at: Optional[float] = None
    awaiting_fresh_entry: bool = False
    read_only: bool = False
    read_only_until: Optional[float] = None
    read_only_reason: Optional[str] = None

    # Runtime flags
    running: bool = False
    armed: bool = False
    trailing_enabled: bool = False
    waiting_for_confirmation: bool = False
    waiting_for_signal: bool = False
    awaiting_user_confirmation: bool = False
    user_confirmed_buy: bool = False
    vortex_signal_ignored: bool = False
    capital_skip_notified: bool = False

    # Strategy params (for UI/debug)
    trailing_pct: float = 0.0
    sma_fast: Optional[float] = None
    sma_slow: Optional[float] = None

    # Balances (for UI/debug)
    usdt_balance: Optional[float] = None
    base_balance: Optional[float] = None

    # Market / price
    last_price: Optional[float] = None
    arm_price: Optional[float] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    trailing_max_price: Optional[float] = None
    vortex_score: Optional[float] = None
    last_signal_ts: Optional[float] = None

    # Trading
    open_position_spent: float = 0.0
    buys_today: int = 0
    spent_today: float = 0.0

    # Results
    last_trade_profit_usdt: float = 0.0
    total_pnl_usdt: float = 0.0

    # Meta
    last_action: str = "INIT"
    last_update: Optional[datetime] = None

    # Adaptive control
    adaptive_state: str = "NORMAL"
    adaptive_reason: Optional[str] = None
    adaptive_prev_state: Optional[str] = None
    adaptive_max_buys_per_day: Optional[int] = None
    adaptive_cooldown_after_sell_seconds: Optional[float] = None
    adaptive_sleep_until: Optional[float] = None

    # AI / Analysis
    ai_mode: str = "SHADOW"
    ai_market_regime: Optional[str] = None
    ai_regime_confidence: Optional[float] = None
    ai_win_rate_60m: Optional[float] = None
    ai_avg_pnl_60m: Optional[float] = None
    ai_pnl_slope_60m: Optional[float] = None
    ai_max_drawdown_60m: Optional[float] = None
    ai_trades_60m: Optional[int] = None
    ai_score_60m: Optional[float] = None
    ai_last_decision: Optional[str] = None
    ai_last_reason: Optional[str] = None
    ai_blocked_by_ai: Optional[bool] = None
    ai_enabled: bool = False
    ai_snapshot_started_at: Optional[float] = None
    ai_recommendation: Optional[dict] = None
    ai_confidence: Optional[float] = None
    ai_last_decision_at: Optional[float] = None
    ai_override: bool = False
    ai_override_reason: Optional[str] = None
    ai_pending_recommendation: bool = False
    ai_last_recommendation_id: Optional[str] = None
    ai_last_recommendation_message_id: Optional[int] = None

    # Telegram
    telegram_message_id: Optional[int] = None
    last_dashboard_hash: Optional[str] = None
    last_dashboard_update: float = 0.0

    # --- SIMULATION ---
    virtual_capital: float = 1.0
    virtual_qty: float = 0.0
    virtual_entry_price: Optional[float] = None
    virtual_max_price: Optional[float] = None
    virtual_pnl: float = 0.0
    virtual_peak_pnl: float = 0.0

    # Simulation stats
    trades_count: int = 0
    wins: int = 0
    losses: int = 0
    total_win: float = 0.0
    total_loss: float = 0.0
    recent_pnls: list[float] = field(default_factory=list)
    max_drawdown: float = 0.0
    armed_notified: bool = False

    # Live trading safety
    real_capital_enabled: bool = False
    real_capital_limit: float = 5.0
    real_drawdown_pct: float = 0.0
    live_disabled_notified: bool = False
