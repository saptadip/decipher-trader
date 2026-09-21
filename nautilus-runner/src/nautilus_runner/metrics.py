"""Reusable performance metrics computed from a realized-PnL history.

Both functions are stopgaps for Nautilus rc5, where
``nautilus_trader.analysis.SharpeRatio.calculate_from_realized_pnls`` and
``MaxDrawdown.calculate_from_realized_pnls`` return ``None`` (the pnl path is
not yet implemented in the Rust port). Swap them out when the rc5 Rust path
lands upstream.
"""

from __future__ import annotations

import statistics


def sharpe_from_pnls(realized_pnls: list[float]) -> float:
    """Per-trade Sharpe ratio: ``mean(pnl) / stdev(pnl)``.

    Returns 0.0 when fewer than two data points are available (stdev undefined)
    or when stdev is exactly 0.0 (all trades identical PnL). The exact
    ``== 0.0`` check is intentional; near-equal-float PnLs from floating-point
    arithmetic are out of scope (real trades never produce bit-identical values).
    """
    if len(realized_pnls) < 2:
        return 0.0
    mean = statistics.mean(realized_pnls)
    stdev = statistics.stdev(realized_pnls)
    if stdev == 0.0:
        return 0.0
    return mean / stdev


def max_drawdown_from_pnls(realized_pnls: list[float]) -> float:
    """Peak-to-trough drawdown from a realized-PnL history.

    Tracks the running cumulative PnL curve and returns the maximum observed
    drop from a peak as a non-negative value. Initial peak = 0.0 so a strategy
    that starts with a losing trade correctly reports a positive drawdown from
    zero. Returns 0.0 when history is empty.
    """
    if not realized_pnls:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    cumulative = 0.0
    for pnl in realized_pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative  # positive: loss from the running peak
        if dd > max_dd:
            max_dd = dd
    return max_dd
