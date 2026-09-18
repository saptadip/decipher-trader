from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId

from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig


@pytest.mark.skip(
    reason="Wired up during implementation once Nautilus backtest fixtures for HYPERLIQUID testnet are added"
)
def test_toy_momentum_backtest_no_crash():
    # Placeholder — the actual backtest wiring is added once a mocked Hyperliquid feed is stood up.
    pass


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
