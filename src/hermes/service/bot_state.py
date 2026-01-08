from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class BotRuntimeState:
    symbol: str
    profile: str

    running: bool = False

    # Market / price
    last_price: Optional[float] = None
    arm_price: Optional[float] = None
    entry_price: Optional[float] = None

    last_rendered_text: str | None = None

    # Strategy config (READ-ONLY snapshot)
    trailing_pct: float = 0.0

    # Status flags
    armed: bool = False
    trailing_enabled: bool = False
    waiting_for_confirmation: bool = False
    waiting_for_signal: bool = False

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

    # Telegram
    telegram_message_id: Optional[int] = None
