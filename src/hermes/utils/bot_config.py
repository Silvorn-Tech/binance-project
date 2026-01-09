# utils/bot_config.py
from dataclasses import dataclass


@dataclass(frozen=True)
class BotConfig:
    # Identity
    bot_id: str
    symbol: str
    base_asset: str
    profile: str

    # Capital & risk
    capital_pct: float
    trade_pct: float
    min_trade_usdt: float
    max_buys_per_day: int
    daily_budget_usdt: float
    disable_max_buys_per_day: bool
    disable_daily_budget: bool

    # Strategy
    sma_fast: int
    sma_slow: int
    kline_interval: str
    kline_limit: int

    # Exit management
    trailing_pct: float
    new_high_epsilon_pct: float
    cooldown_after_sell_seconds: float

    # Trend exit (optional but default ON)
    trend_exit_enabled: bool = True
    trend_sma_period: int = 25
    max_hold_seconds_without_new_high: float = 300.0
