"""Minimal demo strategies for exercising the backtest runner end-to-end.

These are **not** production strategies — they exist so ``run_backtest.py`` can
be smoke-tested against a real catalog. Real strategy exploration is Session 3.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Quantity
from nautilus_trader.trading import Strategy


class BuyAndHoldConfig(StrategyConfig):
    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        bar_type: BarType,
        trade_size: Decimal = Decimal("0.001"),
        size_precision: int = 3,
    ) -> None:
        super().__init__()
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.trade_size = trade_size
        self.size_precision = size_precision


class BuyAndHold(Strategy):
    """Buy on first bar, close on last bar. Simplest possible backtest exerciser."""

    def __init__(self, config: BuyAndHoldConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._n_bars = 0

    def on_start(self) -> None:
        self.subscribe_bars(self._cfg.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._n_bars += 1
        if self._n_bars == 1:
            self.submit_order(
                self.order_factory.market(
                    instrument_id=self._cfg.instrument_id,
                    order_side=OrderSide.BUY,
                    quantity=Quantity(float(self._cfg.trade_size), self._cfg.size_precision),
                ),
            )

    def on_stop(self) -> None:
        # Close any remaining position on shutdown.
        if not self.portfolio.is_net_flat(self._cfg.instrument_id):
            self.close_all_positions(self._cfg.instrument_id)
