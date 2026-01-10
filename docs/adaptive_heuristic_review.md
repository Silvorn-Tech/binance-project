# Adaptive Heuristic Review

This doc applies the three requested steps:
1) Mental backtest with 10 recent real trades.
2) Simulated extreme scenarios (chop / dump / fake breakout).
3) Extended heuristics (volatility, range, lateral).

## 1) Mental Backtest (10 Recent Trades)

Run:

```bash
PYTHONPATH=src .venv/bin/python scripts/adaptive_review.py --bot-id YOUR_BOT_ID --limit 10
```

Paste the output below and inspect:
- Loss streak
- Drawdown
- Win rate
- Volatility vs. average move
- Flip rate (sign changes)

Template (fill after running the script):

```
Trades:
1. ...
2. ...
3. ...
...

Metrics:
- win_rate:
- drawdown_pct:
- negative_streak:
- avg_abs_pnl_pct:
- pnl_volatility_pct:
- flip_rate:
Expected state: NORMAL / DEFENSIVE
Reason:
```

## 2) Extreme Scenarios (Mental Simulation)

### Chop (sideways whipsaw)
PnLs (percent of spent): -0.3, +0.2, -0.2, +0.1, -0.2, +0.1
Expected:
- flip_rate high
- avg_abs_pnl_pct low
Outcome: DEFENSIVE (lateral_chop)

### Dump (volatile down)
PnLs: -1.5, -1.2, -0.8, +0.3, -1.0
Expected:
- negative_streak >= 3
- drawdown >= 5%
Outcome: DEFENSIVE (loss_streak / drawdown)

### Fake Breakout (spiky but flat)
PnLs: +0.6, -0.5, +0.4, -0.4, +0.3, -0.3
Expected:
- flip_rate high
- avg_abs_pnl_pct low-to-mid
Outcome: DEFENSIVE (lateral_chop) or NORMAL if thresholds not hit

## 3) Heuristic Extensions (No ML)

Current behavior adds three new risk triggers (Equilibrium only, post-SELL):

- Volatility: if PnL volatility is high and cumulative PnL is negative.
- Range: if average absolute PnL is very low (tight range).
- Lateral: if sign flips are frequent and moves are small.

These are in `src/hermes/utils/adaptive_controller.py` as thresholds that can be tuned
without changing strategy logic.
