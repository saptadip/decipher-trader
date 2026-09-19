from __future__ import annotations

from collections import deque
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Quantity
from nautilus_trader.model import PositionChanged
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
        # Optional: DB row id for metrics emission; passed as **_kwargs by _build_node.
        self.strategy_db_id: int | None = _kwargs.get("strategy_db_id")


_METRICS_INTERVAL_BARS: int = 60


class ToyMomentum(Strategy):
    def __init__(self, config: ToyMomentumConfig) -> None:
        super().__init__(config)
        self._config = config
        self._fast: deque[float] = deque(maxlen=config.fast_period)
        self._slow: deque[float] = deque(maxlen=config.slow_period)
        self._signed_position: float = 0.0

        # Notional cap tracking
        self._last_close: float = 0.0

        # Daily loss circuit
        self._realized_pnl_today: float = 0.0
        self._last_reset_utc_date: date | None = None

        # Metrics
        self._bars_since_metric: int = 0
        self._n_trades: int = 0

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

        # Notional cap: refuse if projected notional exceeds limit.
        if self._last_close != 0.0:
            projected_qty = self._signed_position + delta
            projected_notional = abs(projected_qty) * self._last_close
            if projected_notional > self._config.max_notional:
                self.log.warning(
                    f"skip order: projected notional {projected_notional:.2f} would breach"
                    f" max_notional={self._config.max_notional}"
                )
                return

        # Daily loss circuit: block deepening orders when daily loss cap is breached.
        # Always allow orders that close or reduce existing exposure (opposite sign to position).
        is_deepening = (delta > 0 and self._signed_position >= 0) or (
            delta < 0 and self._signed_position <= 0
        )
        if is_deepening and self._realized_pnl_today <= -self._config.max_daily_loss:
            self.log.warning(
                f"skip order: daily loss cap breached"
                f" (realized_pnl_today={self._realized_pnl_today:.4f},"
                f" max_daily_loss={self._config.max_daily_loss})"
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
        # UTC day boundary reset for daily loss tracking.
        today = datetime.now(timezone.utc).date()
        if self._last_reset_utc_date != today:
            self._realized_pnl_today = 0.0
            self._last_reset_utc_date = today

        price = float(bar.close)
        self._last_close = price
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

        # Metrics emission: every _METRICS_INTERVAL_BARS bars.
        self._bars_since_metric += 1
        if self._bars_since_metric >= _METRICS_INTERVAL_BARS:
            self._bars_since_metric = 0
            self._emit_metric()

    def on_position_changed(self, event: PositionChanged) -> None:
        if event.realized_pnl is not None:
            self._realized_pnl_today += event.realized_pnl.as_double()
        self._n_trades += 1

    def _emit_metric(self) -> None:
        from nautilus_runner import state  # local import to avoid hard dep in tests

        if state.metrics is None or self._config.strategy_db_id is None:
            return

        max_drawdown = abs(min(self._realized_pnl_today, 0.0))
        state.metrics.post_metric(
            strategy_id=self._config.strategy_db_id,
            ts=datetime.now(timezone.utc),
            pnl=self._realized_pnl_today,
            sharpe=0.0,
            max_drawdown=max_drawdown,
            n_trades=self._n_trades,
        )

    def on_stop(self) -> None:
        pass
