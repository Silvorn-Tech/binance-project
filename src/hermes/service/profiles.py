# src/service/profiles.py

PROFILES = {
    # =========================
    # 🛡️ SENTINEL — Conservative
    # =========================
    "sentinel": {
        "capital_pct": 0.20,
        "trade_pct": 0.20,
        "min_trade_usdt": 7.0,
        "disable_max_buys_per_day": False,
        "disable_daily_budget": False,
        "max_buys_per_day": 5,
        "daily_budget_usdt": 40.0,
        "sma_fast": 14,
        "sma_slow": 50,
        "trailing_pct": 0.01,
    },

    # =========================
    # ⚖️ EQUILIBRIUM — Balanced
    # =========================
    "equilibrium": {
        "capital_pct": 0.15,
        "trade_pct": 0.35,
        "min_trade_usdt": 7.0,
        "disable_max_buys_per_day": False,
        "disable_daily_budget": False,
        "max_buys_per_day": 20,
        "daily_budget_usdt": 100.0,
        "sma_fast": 9,
        "sma_slow": 21,
        "trailing_pct": 0.015,
    },

    # =========================
    # 🌪️ VORTEX — Aggressive
    # =========================
    "vortex": {
        "capital_pct": 0.08,
        "trade_pct": 0.9,
        "min_trade_usdt": 7.0,
        "disable_max_buys_per_day": False,
        "disable_daily_budget": False,
        "max_buys_per_day": 60,
        "daily_budget_usdt": 200.0,
        "sma_fast": 5,
        "sma_slow": 13,
        "trailing_pct": 0.03,
    },
}
