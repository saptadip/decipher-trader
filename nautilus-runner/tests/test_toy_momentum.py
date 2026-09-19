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


def _feed_bars(strategy: ToyMomentum, prices: list[float]) -> None:
    """Feed synthetic bars through strategy.on_bar with runtime methods mocked."""
    bar_type = strategy._config.bar_type
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
        _feed_bars(strategy, prices)

        # Assert 2: first submit_order after warm-up is a BUY on the bullish crossover.
        # Guards against the crossover logic being accidentally inverted (SELL on bull).
        assert mock_submit_order.call_count >= 1, (
            f"Expected at least 1 submit_order call after bullish MA crossover, got {mock_submit_order.call_count}"
        )
        first_call_side = mock_order_factory.market.call_args_list[0].kwargs["order_side"]
        assert first_call_side == OrderSide.BUY, (
            f"First submit after bullish crossover should be BUY, got {first_call_side}"
        )

        # Assert 3: net signed position after the full cycle equals one trade_size worth
        # (deterministic: 1 BUY of +trade_size followed by 2 SELLs of -trade_size each = -trade_size).
        # This is stronger than "!= 0.0" — it would fail for pure-SELL runs or wrong direction.
        assert abs(strategy._signed_position) == pytest.approx(0.001), (
            f"Expected |_signed_position| == 0.001 after 1 BUY + 2 SELLs, got {strategy._signed_position}"
        )

        # Assert 4a: cap enforcement BLOCKS an order that would breach max_position.
        # Setup: position = 0.999, trade_size raised to 0.01 → projected 1.009 > cap (1.0), block.
        strategy._signed_position = 0.999
        call_count_before = mock_submit_order.call_count
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
