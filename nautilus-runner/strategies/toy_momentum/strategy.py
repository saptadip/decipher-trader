from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Quantity
from nautilus_trader.trading import Strategy


def would_breach_position_cap(
    current_position: float, delta: float, cap: float
) -> bool:
    """Return True if applying `delta` to `current_position` would push |pos| beyond `cap`."""
    projected = current_position + delta
    return abs(projected) > cap


class ToyMomentumConfig(StrategyConfig):
    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        bar_type: BarType,
        trade_size: Decimal,
        max_notional: float,
        max_daily_loss: float,
        max_position: float,
        fast_period: int = 5,
        slow_period: int = 20,
        **_kwargs: Any,
    ) -> None:
        super().__init__()
        assert slow_period > fast_period, "slow_period must exceed fast_period"
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.trade_size = trade_size
        self.max_notional = max_notional
        self.max_daily_loss = max_daily_loss
        self.max_position = max_position
        self.fast_period = fast_period
        self.slow_period = slow_period


class ToyMomentum(Strategy):
    def __init__(self, config: ToyMomentumConfig) -> None:
        super().__init__(config)
        self._config = config
        self._fast: deque[float] = deque(maxlen=config.fast_period)
        self._slow: deque[float] = deque(maxlen=config.slow_period)
        self._signed_position: float = 0.0

    def on_start(self) -> None:
        self.subscribe_bars(self._config.bar_type)

    def _submit_capped(self, side: OrderSide, delta: float) -> None:
        # G3: refuse to breach the per-strategy position cap.
        if would_breach_position_cap(
            self._signed_position, delta, self._config.max_position
        ):
            self.log.warning(
                f"skip order: would breach max_position={self._config.max_position}"
            )
            return
        self.submit_order(
            self.order_factory.market(
                instrument_id=self._config.instrument_id,
                order_side=side,
                quantity=Quantity.from_str(str(self._config.trade_size)),
            )
        )
        self._signed_position += delta

    def on_bar(self, bar: Bar) -> None:
        price = float(bar.close)
        self._fast.append(price)
        self._slow.append(price)
        if len(self._slow) < self._config.slow_period:
            return

        fast_ma = sum(self._fast) / len(self._fast)
        slow_ma = sum(self._slow) / len(self._slow)

        want_long = fast_ma > slow_ma
        trade_qty = float(self._config.trade_size)
        if want_long and self._signed_position <= 0:
            self._submit_capped(OrderSide.BUY, +trade_qty)
        elif not want_long and self._signed_position >= 0:
            self._submit_capped(OrderSide.SELL, -trade_qty)

    def on_stop(self) -> None:
        pass
