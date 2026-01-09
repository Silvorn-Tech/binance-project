# src/service/bot_builder.py

from hermes.utils.bot_config import BotConfig
from hermes.service.profiles import PROFILES


class BotBuilder:
    def __init__(self):
        self._config: dict = {}

    def with_symbol(self, symbol: str, base_asset: str):
        self._config["symbol"] = symbol.upper()
        self._config["base_asset"] = base_asset.upper()
        return self

    def with_profile(self, profile_name: str):
        if profile_name not in PROFILES:
            raise ValueError(f"Unknown profile: {profile_name}")

        self._config.update(PROFILES[profile_name])
        self._config["profile"] = profile_name
        return self

    def with_defaults(self):
        self._config.setdefault("kline_interval", "1m")
        self._config.setdefault("kline_limit", 60)
        self._config.setdefault("cooldown_after_sell_seconds", 60)
        self._config.setdefault("trend_exit_enabled", True)
        self._config.setdefault("trend_sma_period", 25)
        self._config.setdefault("max_hold_seconds_without_new_high", 5 * 60)
        self._config.setdefault("new_high_epsilon_pct", 0.0002)
        self._config.setdefault("disable_max_buys_per_day", False)
        self._config.setdefault("disable_daily_budget", False)
        return self

    def build(self) -> BotConfig:
        if "bot_id" not in self._config and "profile" in self._config and "base_asset" in self._config:
            bot_id = f"{self._config['profile']}_{self._config['base_asset'].lower()}"
            self._config["bot_id"] = bot_id

        required_fields = [
            "bot_id",
            "symbol",
            "base_asset",
            "profile",
            "capital_pct",
            "trade_pct",
            "min_trade_usdt",
            "max_buys_per_day",
            "daily_budget_usdt",
            "disable_max_buys_per_day",
            "disable_daily_budget",
            "sma_fast",
            "sma_slow",
            "trailing_pct",
            "new_high_epsilon_pct",
            "kline_interval",
            "kline_limit",
            "cooldown_after_sell_seconds",
        ]

        missing = [f for f in required_fields if f not in self._config]
        if missing:
            raise ValueError(f"Missing BotConfig fields: {missing}")

        return BotConfig(**self._config)
