from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Price, Quantity

from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig


def _make_strategy(trade_size: Decimal = Decimal("0.001"), max_position: float = 1.0) -> ToyMomentum:
    instrument = InstrumentId.from_str("BTC-USD.HYPERLIQUID")
    bar_type = BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL")
    config = ToyMomentumConfig(
        instrument_id=instrument,
        bar_type=bar_type,
        trade_size=trade_size,
        max_notional=1000.0,
        max_daily_loss=100.0,
        max_position=max_position,
    )
    return ToyMomentum(config)


def _make_bar(bar_type: BarType, price: float, ts: int) -> Bar:
    p = Price.from_str(f"{price:.1f}")
    q = Quantity.from_str("1.0")
    return Bar(bar_type, p, p, p, p, q, ts, ts)


def _feed_bars(strategy: ToyMomentum, mock_submit: MagicMock, prices: list[float]) -> None:
    """Feed synthetic bars through strategy.on_bar with runtime methods mocked."""
    bar_type = BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL")
    for ts, price in enumerate(prices):
        bar = _make_bar(bar_type, price, ts)
        strategy.on_bar(bar)


def test_toy_momentum_backtest_no_crash():
    """
    Feed 50 synthetic bars (rising 100->124, falling 124->100) through on_bar and assert:
    1. No exception across the full MA crossover cycle.
    2. submit_order called at least once on bullish crossover.
    3. _signed_position tracks the delta after a crossover order.
    4. Position-cap enforcement: in-range orders go through; over-cap orders are blocked.

    ToyMomentumConfig uses fast_period=5, slow_period=20. Warm-up completes at bar 19.
    Fast MA leads slow MA up during bars ~20-24 (bullish crossover -> BUY).
    Fast MA leads slow MA down after ~bar 30 (bearish crossover -> SELL).
    """
    strategy = _make_strategy()
    mock_log = MagicMock()
    mock_order_factory = MagicMock()
    mock_order_factory.market.return_value = MagicMock()
    mock_submit_order = MagicMock()

    prices = (
        [100.0 + i for i in range(25)]   # bars 0-24: 100 -> 124 (rising)
        + [124.0 - i for i in range(25)] # bars 25-49: 124 -> 100 (falling)
    )

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "order_factory", mock_order_factory),
        patch.object(ToyMomentum, "submit_order", mock_submit_order),
    ):
        # Assert 1: no exception across the full 50-bar cycle
        _feed_bars(strategy, mock_submit_order, prices)

        # Assert 2: submit_order called at least once (bullish crossover triggered a BUY)
        assert mock_submit_order.call_count >= 1, (
            f"Expected at least 1 submit_order call after bullish MA crossover, got {mock_submit_order.call_count}"
        )

        # Assert 3: _signed_position is non-zero — position tracking reflects applied delta
        # After the bullish crossover BUY _signed_position should have gone positive,
        # and after the bearish crossover SELL it ends at or near the short side.
        # Verify it moved away from zero at some point by checking the final state is
        # consistent with at least one net trade having been applied.
        assert strategy._signed_position != 0.0, (
            "_signed_position should be non-zero after trades — position tracking is broken"
        )

        # Assert 4a: cap enforcement BLOCKS an order that would breach max_position.
        # Set position near the cap so the next trade would exceed it.
        strategy._signed_position = 0.999  # cap is 1.0; delta 0.001 => projected 1.000, not > 1.0
        call_count_before = mock_submit_order.call_count
        # Use a larger delta (0.01) so 0.999 + 0.01 = 1.009 > 1.0 -> should be blocked
        strategy._config = ToyMomentumConfig(
            instrument_id=InstrumentId.from_str("BTC-USD.HYPERLIQUID"),
            bar_type=BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL"),
            trade_size=Decimal("0.01"),
            max_notional=1000.0,
            max_daily_loss=100.0,
            max_position=1.0,
        )
        strategy._submit_capped(OrderSide.BUY, 0.01)
        assert mock_submit_order.call_count == call_count_before, (
            "submit_order should NOT be called when order would breach max_position"
        )

        # Assert 4b: cap enforcement ALLOWS an order that stays within max_position.
        strategy._signed_position = 0.0   # reset
        strategy._config = ToyMomentumConfig(
            instrument_id=InstrumentId.from_str("BTC-USD.HYPERLIQUID"),
            bar_type=BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL"),
            trade_size=Decimal("0.001"),
            max_notional=1000.0,
            max_daily_loss=100.0,
            max_position=1.0,
        )
        call_count_before = mock_submit_order.call_count
        strategy._submit_capped(OrderSide.BUY, 0.001)
        assert mock_submit_order.call_count == call_count_before + 1, (
            "submit_order SHOULD be called when order stays within max_position"
        )


def test_config_defaults_are_sane():
    instrument = InstrumentId.from_str("BTC-USD.HYPERLIQUID")
    bar_type = BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL")
    config = ToyMomentumConfig(
        instrument_id=instrument,
        bar_type=bar_type,
        trade_size=Decimal("0.001"),
        max_notional=1000.0,
        max_daily_loss=100.0,
        max_position=1.0,
    )
    assert config.fast_period == 5
    assert config.slow_period == 20
    assert config.slow_period > config.fast_period


def test_would_breach_position_cap_returns_true_when_over():
    from strategies.toy_momentum.strategy import (
        ToyMomentumConfig,
        would_breach_position_cap,
    )

    assert would_breach_position_cap(current_position=0.9, delta=0.2, cap=1.0) is True
    assert would_breach_position_cap(current_position=0.5, delta=0.2, cap=1.0) is False
    # symmetric on the short side
    assert would_breach_position_cap(current_position=-0.9, delta=-0.2, cap=1.0) is True
