from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import statistics

from nautilus_trader.common import TimeEvent
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Quantity
from nautilus_trader.model import PositionChanged
from nautilus_trader.trading import Strategy


def _sharpe_from_pnls(realized_pnls: list[float]) -> float:
    """Compute per-trade Sharpe ratio from a realized PnL history.

    ``nautilus_trader.analysis.SharpeRatio.calculate_from_realized_pnls`` returns
    ``None`` in rc5 (the pnl path is not yet implemented in the Rust port), so we
    implement the formula directly: mean(pnl) / std(pnl).

    Returns 0.0 when fewer than two data points are available (std dev undefined)
    or when stdev is exactly 0.0 (all trades identical PnL). The exact ``== 0.0``
    check is intentional; near-equal-float PnLs from floating-point arithmetic
    are out of scope for this MVP (real trades never produce bit-identical values).

    TODO: replace with nautilus_trader.analysis.SharpeRatio.calculate_from_realized_pnls
    when the rc5 Rust path is implemented upstream.
    """
    if len(realized_pnls) < 2:
        return 0.0
    mean = statistics.mean(realized_pnls)
    stdev = statistics.stdev(realized_pnls)
    if stdev == 0.0:
        return 0.0
    return mean / stdev


def _max_drawdown_from_pnls(realized_pnls: list[float]) -> float:
    """Compute cumulative peak-to-trough drawdown from a realized PnL history.

    ``nautilus_trader.analysis.MaxDrawdown.calculate_from_realized_pnls`` returns
    ``None`` in rc5 (the pnl path is not yet implemented in the Rust port), so we
    implement the formula directly: tracks the running cumulative PnL curve and
    returns the maximum observed drop from a peak as a non-negative value.

    Initial peak = 0.0 so a strategy that starts with a losing trade correctly
    reports a positive drawdown from zero. Returns 0.0 when history is empty.

    TODO: replace with nautilus_trader.analysis.MaxDrawdown.calculate_from_realized_pnls
    when the rc5 Rust path is implemented upstream.
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
_RECONCILE_INTERVAL = timedelta(minutes=5)
_DRIFT_THRESHOLD_MULTIPLIER = 2.0  # drift alerts when abs > trade_size * this


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
        # Unbounded MVP: session-scoped realized-PnL history feeds sharpe + max_drawdown
        # computation. At real live-trading frequency (hundreds of trades/day) the O(n)
        # per-emission cost becomes non-trivial; prune to last K entries in Phase 2.
        self._realized_pnl_history: list[float] = []

    def on_start(self) -> None:
        self.subscribe_bars(self._config.bar_type)
        # Seed _signed_position from venue. Nautilus reconciliation completes BEFORE on_start,
        # so portfolio.net_position() reflects the venue-side truth here.
        seeded = float(self.portfolio.net_position(self._config.instrument_id))
        if seeded != 0.0:
            self.log.info(f"adopted venue position at startup: {seeded}")
            from nautilus_runner import state  # local import to avoid hard dep in tests

            if state.audit_writer is not None and self._config.strategy_db_id is not None:
                state.audit_writer.post(
                    actor="runner",
                    action="position_adopted_on_start",
                    payload={
                        "strategy_id": self._config.strategy_db_id,
                        "venue_position": seeded,
                    },
                )
        self._signed_position = seeded
        # Schedule the 5-min reconciler on the Nautilus event loop thread.
        # Use Nautilus's own StrategyId (self.id) so the timer name is unique across
        # any multi-strategy runner without depending on strategy_db_id being set.
        self.clock.set_timer(
            name=f"reconciler-{self.id}",
            interval=_RECONCILE_INTERVAL,
            callback=self._reconcile,
        )

    def _reconcile(self, event: TimeEvent) -> None:
        venue_pos = float(self.portfolio.net_position(self._config.instrument_id))
        runner_pos = self._signed_position
        drift = abs(venue_pos - runner_pos)
        threshold = float(self._config.trade_size) * _DRIFT_THRESHOLD_MULTIPLIER
        if drift <= threshold:
            return
        self.log.warning(
            f"position drift: runner={runner_pos}, venue={venue_pos},"
            f" drift={drift} (threshold={threshold})"
        )
        from nautilus_runner import state  # local import to avoid hard dep in tests

        if state.audit_writer is not None and self._config.strategy_db_id is not None:
            state.audit_writer.post(
                actor="runner",
                action="position_drift",
                payload={
                    "strategy_id": self._config.strategy_db_id,
                    "runner_pos": runner_pos,
                    "venue_pos": venue_pos,
                    "drift": drift,
                    "threshold": threshold,
                },
            )
        if drift > self._config.max_position:
            # Critical: worse than the strategy's own cap. Log ERROR — operator gets
            # Telegram via control-plane's audit-alert chain — but do not self-kill
            # from here (chaos-fragile; let the operator see the alert and decide).
            self.log.error(
                f"CRITICAL position drift ({drift}) exceeds"
                f" max_position ({self._config.max_position})"
            )

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
            pnl_value = event.realized_pnl.as_double()
            self._realized_pnl_today += pnl_value
            self._realized_pnl_history.append(pnl_value)
        self._n_trades += 1

    def _emit_metric(self) -> None:
        from nautilus_runner import state  # local import to avoid hard dep in tests

        if state.metrics is None or self._config.strategy_db_id is None:
            return

        sharpe = _sharpe_from_pnls(self._realized_pnl_history)
        max_drawdown = _max_drawdown_from_pnls(self._realized_pnl_history)

        state.metrics.post_metric(
            strategy_id=self._config.strategy_db_id,
            ts=datetime.now(timezone.utc),
            pnl=self._realized_pnl_today,
            sharpe=sharpe,
            max_drawdown=max_drawdown,
            n_trades=self._n_trades,
        )

    def on_stop(self) -> None:
        # Defensive: cancel the reconciler timer so a hypothetical restart of
        # the same strategy instance doesn't hit a duplicate-registration error.
        try:
            self.clock.cancel_timer(f"reconciler-{self.id}")
        except Exception:
            pass
        # Flatten before shutdown: cancel any outstanding orders and close open
        # positions on our instrument. Nautilus routes these as market orders to
        # the venue. Best-effort — a network failure here must not prevent stop.
        try:
            self.cancel_all_orders(self._config.instrument_id)
        except Exception:
            self.log.warning("cancel_all_orders failed during on_stop")
        try:
            self.close_all_positions(self._config.instrument_id)
        except Exception:
            self.log.warning("close_all_positions failed during on_stop")
        # Audit the flatten so the operator sees it in the log alongside kill_all.
        from nautilus_runner import state  # local import to avoid hard dep in tests

        if state.audit_writer is not None and self._config.strategy_db_id is not None:
            state.audit_writer.post(
                actor="runner",
                action="flatten_on_stop",
                payload={
                    "strategy_id": self._config.strategy_db_id,
                    "instrument": str(self._config.instrument_id),
                },
            )
