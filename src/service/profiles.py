# src/service/profiles.py

PROFILES = {
    # =========================
    # 🛡️ SENTINEL — Conservative
    # =========================
    "sentinel": {
        "buy_usdt": 7.0,
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
        "buy_usdt": 7.0,
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
        "buy_usdt": 7.0,
        "max_buys_per_day": 60,
        "daily_budget_usdt": 200.0,
        "sma_fast": 5,
        "sma_slow": 13,
        "trailing_pct": 0.03,
    },
}
