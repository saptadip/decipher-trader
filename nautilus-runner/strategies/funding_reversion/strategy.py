"""Funding-rate mean-reversion strategy for BTC perpetuals.

Hypothesis
----------
On a perpetual venue, funding fires periodically (Binance USDM: every 8h). When
funding is deeply positive, longs pay shorts — indicating long-side crowding —
and price often mean-reverts down as the crowded side unwinds. Symmetric on
the short side. This strategy takes the opposite side of the crowded flow at
extreme funding and closes when funding reverts toward zero.

Signal
------
Poll ``funding_events`` (a pre-loaded list, chronological) on every bar
callback. For each event whose ``ts_ns`` has passed since the last bar:

- If flat:
  - ``rate >= +entry_threshold`` → SELL (short)
  - ``rate <= -entry_threshold`` → BUY (long)
- If in position:
  - ``|rate| <= exit_threshold`` → close (mean-reverted)

The strategy holds at most one directional position; a fresh entry signal
while in position is ignored (no pyramiding).

Position caps mirror ``ToyMomentum``: ``max_position`` (units) and
``max_notional`` (quote) block sizing errors; ``max_daily_loss`` is a soft
circuit that refuses new entries after the day's realized PnL crosses the
threshold. Backtest-safe today; live-mode hardening (venue-position seed at
``on_start``, reconciler timer, ``cancel_all_orders`` at ``on_stop``, audit
writer + metrics writer wiring, socket-disconnect handling) is deferred —
see the ToyMomentum live-plumbing walkthrough from Session-2 PR-B for the
pattern before promoting this strategy to paper mode.

Bar-interval assumption
-----------------------

Assumes ``bar_interval < funding_interval`` (Binance USDM funding is 8h).
At 1h / 4h bars, at most one funding event falls inside any bar. At bar
intervals ``>= 8h``, multiple events can arrive in the same bar and the
``while`` loop below would enter → exit → re-enter within the bar, paying
fees for no real exposure. The CLIs currently expose ``1m``/``5m``/``15m``/
``1h``/``4h`` (all safe) and ``1d`` (unsafe — do not use with this strategy).
"""

from __future__ import annotations

from collections import deque
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Quantity
from nautilus_trader.model import PositionChanged
from nautilus_trader.trading import Strategy


class FundingReversionConfig(StrategyConfig):
    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        bar_type: BarType,
        funding_events: list[dict[str, Any]],
        entry_threshold: float,
        exit_threshold: float,
        trade_size: Decimal,
        max_notional: float,
        max_daily_loss: float,
        max_position: float,
        **_kwargs: Any,
    ) -> None:
        super().__init__()
        if entry_threshold <= 0:
            raise ValueError(f"entry_threshold must be > 0, got {entry_threshold}")
        if exit_threshold < 0:
            raise ValueError(f"exit_threshold must be >= 0, got {exit_threshold}")
        if exit_threshold >= entry_threshold:
            raise ValueError(
                f"exit_threshold ({exit_threshold}) must be strictly less than "
                f"entry_threshold ({entry_threshold})"
            )
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.funding_events = funding_events
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.trade_size = trade_size
        self.max_notional = max_notional
        self.max_daily_loss = max_daily_loss
        self.max_position = max_position
        self.strategy_db_id: int | None = _kwargs.get("strategy_db_id")


class FundingReversion(Strategy):
    def __init__(self, config: FundingReversionConfig) -> None:
        super().__init__(config)
        self._cfg = config
        # Chronologically-sorted deque so we can consume in O(1) per event.
        events = sorted(config.funding_events, key=lambda e: int(e["ts_ns"]))
        self._events: deque[dict[str, Any]] = deque(events)
        self._signed_position: float = 0.0
        self._last_close: float = 0.0

        self._realized_pnl_today: float = 0.0
        self._last_reset_utc_date: date | None = None
        # NOTE: the daily-loss reset below uses `datetime.now(timezone.utc)` —
        # wall clock, not the simulated clock. Same pre-existing pattern as
        # ToyMomentum; a follow-up PR will migrate both to `self.clock.utc_now()`
        # so backtests are deterministic across UTC-midnight crossings.

    def on_start(self) -> None:
        self.subscribe_bars(self._cfg.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._last_close = float(bar.close)
        ts = int(bar.ts_event)
        # Consume any funding events that fired since the previous bar.
        # bar.ts_event is the bar-close timestamp under Nautilus's default
        # (LeftOpen, timestamp_on_close) bar semantics, so a funding event at
        # exactly the bar's close ns fires on that bar and its resulting market
        # order fills at the OPEN of the next bar — no look-ahead.
        while self._events and int(self._events[0]["ts_ns"]) <= ts:
            event = self._events.popleft()
            self._on_funding(event, bar)

    def _on_funding(self, event: dict[str, Any], bar: Bar) -> None:
        rate = float(event["funding_rate"])
        self._reset_daily_pnl_if_new_day(datetime.now(timezone.utc).date())

        if self._signed_position == 0.0:
            if self._deep_loss_today():
                return
            if rate >= self._cfg.entry_threshold:
                self._enter(OrderSide.SELL)
            elif rate <= -self._cfg.entry_threshold:
                self._enter(OrderSide.BUY)
            return

        # In-position: exit on mean-reversion.
        if abs(rate) <= self._cfg.exit_threshold:
            side = OrderSide.SELL if self._signed_position > 0 else OrderSide.BUY
            self._submit_capped(side, self._cfg.trade_size)

    def _deep_loss_today(self) -> bool:
        return self._realized_pnl_today <= -self._cfg.max_daily_loss

    def _reset_daily_pnl_if_new_day(self, today: date) -> None:
        if self._last_reset_utc_date != today:
            self._realized_pnl_today = 0.0
            self._last_reset_utc_date = today

    def _enter(self, side: OrderSide) -> None:
        self._submit_capped(side, self._cfg.trade_size)

    def _submit_capped(self, side: OrderSide, qty: Decimal) -> None:
        """Submit a market order with cap enforcement.

        ``qty`` is a positive ``Decimal`` — the caller passes ``trade_size``
        directly and this method applies the sign based on ``side``.
        """
        qty_float = float(qty)
        delta = qty_float if side is OrderSide.BUY else -qty_float
        projected = self._signed_position + delta
        # Position cap
        if abs(projected) > self._cfg.max_position:
            self.log.warning(
                f"skip {side.name}: projected position {projected} would breach "
                f"max_position ({self._cfg.max_position})"
            )
            return
        # Notional cap
        if self._last_close > 0.0:
            projected_notional = abs(projected) * self._last_close
            if projected_notional > self._cfg.max_notional:
                self.log.warning(
                    f"skip {side.name}: projected notional {projected_notional} would "
                    f"breach max_notional ({self._cfg.max_notional})"
                )
                return

        # AGENTS.md exact-arithmetic rule: quantity through Decimal, not float.
        # ``trade_size.as_tuple().exponent`` is negative for sub-integer values
        # (e.g. ``Decimal("0.001").as_tuple().exponent == -3``); the precision
        # for ``Quantity.from_decimal_dp`` is the positive form.
        precision = -self._cfg.trade_size.as_tuple().exponent
        order = self.order_factory.market(
            instrument_id=self._cfg.instrument_id,
            order_side=side,
            quantity=Quantity.from_decimal_dp(qty, precision),
        )
        self.submit_order(order)
        self._signed_position += delta

    def on_position_changed(self, event: PositionChanged) -> None:
        try:
            realized = float(event.realized_pnl) if event.realized_pnl is not None else 0.0
        except (TypeError, ValueError):
            realized = 0.0
        self._realized_pnl_today += realized

    def on_stop(self) -> None:
        try:
            if not self.portfolio.is_net_flat(self._cfg.instrument_id):
                self.close_all_positions(self._cfg.instrument_id)
        except Exception:
            self.log.exception("FundingReversion: close_all_positions failed on stop")
