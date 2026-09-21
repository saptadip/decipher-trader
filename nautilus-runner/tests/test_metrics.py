"""Unit tests for nautilus_runner.metrics.

These pin the formulas against hand-computed answers so a future swap-in of
``nautilus_trader.analysis.SharpeRatio`` / ``MaxDrawdown`` (once rc5 lands the
Rust pnl path) cannot silently change the numeric contract without breaking a
test.
"""

import pytest

from nautilus_runner.metrics import max_drawdown_from_pnls, sharpe_from_pnls


def test_sharpe_from_pnls_zero_when_all_entries_identical():
    """Identical PnLs → stdev exactly 0.0 → Sharpe 0.0 (guarded division)."""
    assert sharpe_from_pnls([5.0, 5.0]) == 0.0
    assert sharpe_from_pnls([-2.5, -2.5, -2.5]) == 0.0


def test_sharpe_from_pnls_empty_and_single_entry_return_zero():
    """Sharpe undefined without at least two data points."""
    assert sharpe_from_pnls([]) == 0.0
    assert sharpe_from_pnls([42.0]) == 0.0


def test_sharpe_from_pnls_matches_hand_computed_value():
    """Hand-computed Sharpe for a known-good sequence."""
    # mean = 5.0; sample stdev ≈ 8.6313; sharpe = 5.0 / 8.6313 ≈ 0.5793.
    assert sharpe_from_pnls([10.0, -5.0, 15.0, -3.0, 8.0]) == pytest.approx(0.5793, rel=1e-3)


def test_max_drawdown_from_pnls_single_entry_negative_reports_loss_from_zero():
    """A single negative first trade starts drawn down from initial peak=0.0."""
    # peak=0.0, cumulative=-5.0 → dd=5.0.
    assert max_drawdown_from_pnls([-5.0]) == pytest.approx(5.0)


def test_max_drawdown_from_pnls_single_entry_positive_is_zero():
    """A single positive first trade is a new peak — no drawdown."""
    assert max_drawdown_from_pnls([10.0]) == 0.0


def test_max_drawdown_from_pnls_empty_is_zero():
    """Empty history has no drawdown."""
    assert max_drawdown_from_pnls([]) == 0.0


def test_max_drawdown_from_pnls_monotonic_up_is_zero():
    """A monotonically increasing equity curve has zero drawdown."""
    assert max_drawdown_from_pnls([1.0, 2.0, 3.0, 4.0]) == 0.0


def test_max_drawdown_from_pnls_matches_hand_computed_value():
    """Hand-computed peak-to-trough for a known-good sequence."""
    # Cumulative: 10, 15, -5, -2 → peak=15, trough=-5, drawdown=20.
    assert max_drawdown_from_pnls([10.0, 5.0, -20.0, 3.0]) == pytest.approx(20.0)
