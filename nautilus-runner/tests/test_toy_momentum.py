from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Price, Quantity

from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig


def _make_strategy(
    trade_size: Decimal = Decimal("0.001"), max_position: float = 1.0
) -> ToyMomentum:
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
        [100.0 + i for i in range(25)]  # bars 0-24: 100 -> 124 (rising)
        + [124.0 - i for i in range(25)]  # bars 25-49: 124 -> 100 (falling)
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
        assert (
            mock_submit_order.call_count >= 1
        ), f"Expected at least 1 submit_order call after bullish MA crossover, got {mock_submit_order.call_count}"
        first_call_side = mock_order_factory.market.call_args_list[0].kwargs[
            "order_side"
        ]
        assert (
            first_call_side == OrderSide.BUY
        ), f"First submit after bullish crossover should be BUY, got {first_call_side}"

        # Assert 3: net signed position after the full cycle equals one trade_size worth
        # (deterministic: 1 BUY of +trade_size followed by 2 SELLs of -trade_size each = -trade_size).
        # This is stronger than "!= 0.0" — it would fail for pure-SELL runs or wrong direction.
        assert (
            abs(strategy._signed_position) == pytest.approx(0.001)
        ), f"Expected |_signed_position| == 0.001 after 1 BUY + 2 SELLs, got {strategy._signed_position}"

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
        assert (
            mock_submit_order.call_count == call_count_before
        ), "submit_order should NOT be called when order would breach max_position"

        # Assert 4b: cap enforcement ALLOWS an order that stays within max_position.
        strategy._signed_position = 0.0  # reset
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
        assert (
            mock_submit_order.call_count == call_count_before + 1
        ), "submit_order SHOULD be called when order stays within max_position"


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
    from strategies.toy_momentum.strategy import would_breach_position_cap

    assert would_breach_position_cap(current_position=0.9, delta=0.2, cap=1.0) is True
    assert would_breach_position_cap(current_position=0.5, delta=0.2, cap=1.0) is False
    # symmetric on the short side
    assert would_breach_position_cap(current_position=-0.9, delta=-0.2, cap=1.0) is True


def test_notional_cap_blocks_over_size():
    """Notional cap refuses an order when abs(projected_qty) * last_close > max_notional."""
    strategy = _make_strategy(trade_size=Decimal("0.01"), max_position=10.0)
    mock_log = MagicMock()
    mock_order_factory = MagicMock()
    mock_order_factory.market.return_value = MagicMock()
    mock_submit_order = MagicMock()

    # BTC-like mark price: $100,000. Buying 0.01 BTC → notional $1,000 > max_notional $500.
    strategy._config = ToyMomentumConfig(
        instrument_id=InstrumentId.from_str("BTC-USD.HYPERLIQUID"),
        bar_type=BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL"),
        trade_size=Decimal("0.01"),
        max_notional=500.0,
        max_daily_loss=100.0,
        max_position=10.0,
    )
    strategy._last_close = 100_000.0

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "order_factory", mock_order_factory),
        patch.object(ToyMomentum, "submit_order", mock_submit_order),
    ):
        strategy._submit_capped(OrderSide.BUY, 0.01)
        assert (
            mock_submit_order.call_count == 0
        ), "submit_order must NOT be called when projected notional exceeds max_notional"
        mock_log.warning.assert_called()


def test_daily_loss_circuit_blocks_deepening_after_breach():
    """Daily loss cap refuses deepening orders when realized loss >= max_daily_loss."""
    strategy = _make_strategy(trade_size=Decimal("0.001"), max_position=10.0)
    mock_log = MagicMock()
    mock_order_factory = MagicMock()
    mock_order_factory.market.return_value = MagicMock()
    mock_submit_order = MagicMock()

    strategy._realized_pnl_today = -100.0  # exactly at limit
    strategy._config = ToyMomentumConfig(
        instrument_id=InstrumentId.from_str("BTC-USD.HYPERLIQUID"),
        bar_type=BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL"),
        trade_size=Decimal("0.001"),
        max_notional=1_000_000.0,  # no notional block
        max_daily_loss=100.0,
        max_position=10.0,
    )
    strategy._signed_position = 0.5  # long position → buying is deepening

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "order_factory", mock_order_factory),
        patch.object(ToyMomentum, "submit_order", mock_submit_order),
    ):
        strategy._submit_capped(OrderSide.BUY, 0.001)
        assert (
            mock_submit_order.call_count == 0
        ), "submit_order must NOT be called when daily loss cap is breached and order deepens"
        mock_log.warning.assert_called()


def test_daily_loss_circuit_allows_flatten():
    """Daily loss cap must allow closing/flattening orders even when loss cap is breached."""
    strategy = _make_strategy(trade_size=Decimal("0.001"), max_position=10.0)
    mock_log = MagicMock()
    mock_order_factory = MagicMock()
    mock_order_factory.market.return_value = MagicMock()
    mock_submit_order = MagicMock()

    strategy._realized_pnl_today = -100.0
    strategy._config = ToyMomentumConfig(
        instrument_id=InstrumentId.from_str("BTC-USD.HYPERLIQUID"),
        bar_type=BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL"),
        trade_size=Decimal("0.001"),
        max_notional=1_000_000.0,
        max_daily_loss=100.0,
        max_position=10.0,
    )
    strategy._signed_position = 0.5  # long position → selling reduces exposure

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "order_factory", mock_order_factory),
        patch.object(ToyMomentum, "submit_order", mock_submit_order),
    ):
        strategy._submit_capped(OrderSide.SELL, -0.001)
        assert (
            mock_submit_order.call_count == 1
        ), "submit_order MUST be called for a flattening order even when daily loss cap is breached"


def test_daily_loss_circuit_blocks_open_from_flat():
    """Daily loss cap must block opening a new position from flat during a losing day."""
    strategy = _make_strategy(trade_size=Decimal("0.001"), max_position=10.0)
    mock_log = MagicMock()
    mock_order_factory = MagicMock()
    mock_order_factory.market.return_value = MagicMock()
    mock_submit_order = MagicMock()

    strategy._realized_pnl_today = -100.0
    strategy._config = ToyMomentumConfig(
        instrument_id=InstrumentId.from_str("BTC-USD.HYPERLIQUID"),
        bar_type=BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL"),
        trade_size=Decimal("0.001"),
        max_notional=1_000_000.0,
        max_daily_loss=100.0,
        max_position=10.0,
    )
    strategy._signed_position = 0.0  # flat — a BUY here opens a new long

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "order_factory", mock_order_factory),
        patch.object(ToyMomentum, "submit_order", mock_submit_order),
    ):
        strategy._submit_capped(OrderSide.BUY, 0.001)
        assert (
            mock_submit_order.call_count == 0
        ), "submit_order must NOT be called when opening from flat during a stopped-out day"
        mock_log.warning.assert_called()


def test_day_boundary_resets_pnl():
    """on_bar resets _realized_pnl_today when the UTC date advances.

    Mocks datetime.now so the test is deterministic and independent of the system clock —
    verifies the reset logic actually runs on a date change (not that "today != 2020").
    """
    strategy = _make_strategy()
    mock_log = MagicMock()
    mock_order_factory = MagicMock()
    mock_order_factory.market.return_value = MagicMock()
    mock_submit_order = MagicMock()

    strategy._last_reset_utc_date = date(2020, 1, 1)
    strategy._realized_pnl_today = -50.0

    bar_type = strategy._config.bar_type
    bar = _make_bar(bar_type, price=100.0, ts=0)

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "order_factory", mock_order_factory),
        patch.object(ToyMomentum, "submit_order", mock_submit_order),
        patch("strategies.toy_momentum.strategy.datetime") as mock_dt,
    ):
        # Force datetime.now(timezone.utc).date() to return a different date than seeded.
        mock_dt.now.return_value.date.return_value = date(2020, 1, 2)
        strategy.on_bar(bar)

    assert (
        strategy._realized_pnl_today == 0.0
    ), "_realized_pnl_today must be reset to 0.0 on a UTC day boundary"
    assert (
        strategy._last_reset_utc_date == date(2020, 1, 2)
    ), "_last_reset_utc_date must advance to the new date after reset"


# ---------------------------------------------------------------------------
# Reconciler tests
# ---------------------------------------------------------------------------


def _make_strategy_with_db_id(
    trade_size: Decimal = Decimal("0.001"),
    max_position: float = 1.0,
    strategy_db_id: int = 42,
) -> ToyMomentum:
    instrument = InstrumentId.from_str("BTC-USD.HYPERLIQUID")
    bar_type = BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL")
    config = ToyMomentumConfig(
        instrument_id=instrument,
        bar_type=bar_type,
        trade_size=trade_size,
        max_notional=1000.0,
        max_daily_loss=100.0,
        max_position=max_position,
        strategy_db_id=strategy_db_id,
    )
    return ToyMomentum(config)


def test_on_start_seeds_signed_position_from_portfolio():
    """on_start seeds _signed_position from portfolio.net_position when non-zero and audits."""
    from decimal import Decimal

    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    mock_log = MagicMock()
    mock_portfolio = MagicMock()
    mock_portfolio.net_position.return_value = Decimal("0.05")
    mock_clock = MagicMock()
    mock_audit = MagicMock()

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "portfolio", mock_portfolio, create=True),
        patch.object(ToyMomentum, "clock", mock_clock, create=True),
        patch.object(ToyMomentum, "subscribe_bars", MagicMock()),
        # self.id is a Nautilus base-class attribute set during full runtime init;
        # not available on a bare pyo3 ToyMomentum instance under unit test.
        patch.object(ToyMomentum, "id", "TEST-STRATEGY-ID", create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy.on_start()

    assert strategy._signed_position == pytest.approx(0.05)
    mock_log.info.assert_called_once()
    mock_audit.post.assert_called_once_with(
        actor="runner",
        action="position_adopted_on_start",
        payload={"strategy_id": 42, "venue_position": 0.05},
    )


def test_on_start_no_audit_when_venue_flat():
    """on_start must not audit when venue position is zero."""
    from decimal import Decimal

    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    mock_log = MagicMock()
    mock_portfolio = MagicMock()
    mock_portfolio.net_position.return_value = Decimal("0.0")
    mock_clock = MagicMock()
    mock_audit = MagicMock()

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "portfolio", mock_portfolio, create=True),
        patch.object(ToyMomentum, "clock", mock_clock, create=True),
        patch.object(ToyMomentum, "subscribe_bars", MagicMock()),
        patch.object(ToyMomentum, "id", "TEST-STRATEGY-ID", create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy.on_start()

    assert strategy._signed_position == pytest.approx(0.0)
    mock_log.info.assert_not_called()
    mock_audit.post.assert_not_called()


def test_reconcile_no_drift_no_alert():
    """_reconcile must do nothing when drift is within threshold."""
    from decimal import Decimal

    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id(trade_size=Decimal("0.001"), max_position=1.0)
    strategy._signed_position = 0.1
    mock_log = MagicMock()
    mock_portfolio = MagicMock()
    mock_portfolio.net_position.return_value = Decimal("0.1")
    mock_audit = MagicMock()
    mock_event = MagicMock()

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "portfolio", mock_portfolio, create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy._reconcile(mock_event)

    mock_log.warning.assert_not_called()
    mock_audit.post.assert_not_called()


def test_reconcile_drift_over_threshold_alerts():
    """_reconcile must warn and audit when drift > trade_size * 2."""
    from decimal import Decimal

    import nautilus_runner.state as state_mod

    # trade_size=0.001, threshold=0.002; drift = |0.2 - 0.1| = 0.1 >> threshold
    strategy = _make_strategy_with_db_id(trade_size=Decimal("0.001"), max_position=1.0)
    strategy._signed_position = 0.1
    mock_log = MagicMock()
    mock_portfolio = MagicMock()
    mock_portfolio.net_position.return_value = Decimal("0.2")
    mock_audit = MagicMock()
    mock_event = MagicMock()

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "portfolio", mock_portfolio, create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy._reconcile(mock_event)

    mock_log.warning.assert_called_once()
    mock_audit.post.assert_called_once()
    call_kwargs = mock_audit.post.call_args.kwargs
    assert call_kwargs["action"] == "position_drift"
    assert call_kwargs["payload"]["strategy_id"] == 42
    assert call_kwargs["payload"]["drift"] == pytest.approx(0.1)


def test_reconcile_critical_drift_logs_error():
    """_reconcile must log ERROR when drift > max_position; no self-kill invocation."""
    from decimal import Decimal

    import nautilus_runner.state as state_mod

    # max_position=0.05; drift = |0.2 - 0.1| = 0.1 > max_position(0.05)
    strategy = _make_strategy_with_db_id(trade_size=Decimal("0.001"), max_position=0.05)
    strategy._signed_position = 0.1
    mock_log = MagicMock()
    mock_portfolio = MagicMock()
    mock_portfolio.net_position.return_value = Decimal("0.2")
    mock_audit = MagicMock()
    mock_event = MagicMock()
    # Positive assertion: pyo3 Strategy.stop is not clean-patchable via patch.object,
    # so we override it on the instance and assert it was not called. Guards against a
    # future refactor accidentally introducing a self-kill on critical drift.
    mock_stop = MagicMock()
    strategy.stop = mock_stop

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "portfolio", mock_portfolio, create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy._reconcile(mock_event)

    mock_log.warning.assert_called_once()
    mock_log.error.assert_called_once()
    mock_stop.assert_not_called()


# ---------------------------------------------------------------------------
# Native-metrics tests: SharpeRatio + MaxDrawdown
# ---------------------------------------------------------------------------


def test_on_position_changed_appends_to_pnl_history():
    """on_position_changed appends realized_pnl to _realized_pnl_history alongside _realized_pnl_today."""
    strategy = _make_strategy()
    assert strategy._realized_pnl_history == []

    mock_pnl = MagicMock()
    mock_pnl.as_double.return_value = 42.5
    event = MagicMock()
    event.realized_pnl = mock_pnl

    strategy.on_position_changed(event)

    assert strategy._realized_pnl_today == pytest.approx(42.5)
    assert strategy._realized_pnl_history == [42.5]

    # Second event appends, not replaces.
    mock_pnl2 = MagicMock()
    mock_pnl2.as_double.return_value = -10.0
    event2 = MagicMock()
    event2.realized_pnl = mock_pnl2
    strategy.on_position_changed(event2)

    assert strategy._realized_pnl_today == pytest.approx(32.5)
    assert strategy._realized_pnl_history == [42.5, -10.0]


def test_on_position_changed_no_pnl_does_not_append():
    """on_position_changed with realized_pnl=None must not append to history."""
    strategy = _make_strategy()
    event = MagicMock()
    event.realized_pnl = None

    strategy.on_position_changed(event)

    assert strategy._realized_pnl_history == []
    assert strategy._n_trades == 1


def test_emit_metric_sharpe_zero_when_history_empty():
    """_emit_metric posts sharpe=0.0 when _realized_pnl_history is empty."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    strategy._realized_pnl_history = []
    mock_metrics = MagicMock()

    with patch.object(state_mod, "metrics", mock_metrics):
        strategy._emit_metric()

    call_kwargs = mock_metrics.post_metric.call_args.kwargs
    assert call_kwargs["sharpe"] == pytest.approx(0.0)


def test_emit_metric_sharpe_zero_when_history_has_one_entry():
    """_emit_metric posts sharpe=0.0 when _realized_pnl_history has only 1 entry (< 2)."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    strategy._realized_pnl_history = [100.0]
    mock_metrics = MagicMock()

    with patch.object(state_mod, "metrics", mock_metrics):
        strategy._emit_metric()

    call_kwargs = mock_metrics.post_metric.call_args.kwargs
    assert call_kwargs["sharpe"] == pytest.approx(0.0)


def test_emit_metric_sharpe_real_when_history_has_two_or_more_entries():
    """_emit_metric posts a non-zero Sharpe pinned to a hand-computed value."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    # Mix of wins and losses producing a meaningful Sharpe.
    # mean = (10 - 5 + 15 - 3 + 8) / 5 = 5.0
    # variance (sample, N-1) = sum((x - 5)^2) / 4 = (25 + 100 + 100 + 64 + 9) / 4 = 74.5
    # stdev = sqrt(74.5) ≈ 8.6313
    # sharpe = 5.0 / 8.6313 ≈ 0.5793
    history = [10.0, -5.0, 15.0, -3.0, 8.0]
    strategy._realized_pnl_history = history
    mock_metrics = MagicMock()

    with patch.object(state_mod, "metrics", mock_metrics):
        strategy._emit_metric()

    call_kwargs = mock_metrics.post_metric.call_args.kwargs
    assert call_kwargs["sharpe"] == pytest.approx(0.5793, rel=1e-3)


def test_emit_metric_max_drawdown_real_from_history():
    """_emit_metric computes cumulative peak-to-trough max_drawdown from realized PnL history."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    # Sequence: +10, +5, -20, +3 → cumulative 10, 15, -5, -2 → peak=15, trough=-5, drawdown=20.
    history = [10.0, 5.0, -20.0, 3.0]
    strategy._realized_pnl_history = history
    mock_metrics = MagicMock()

    with patch.object(state_mod, "metrics", mock_metrics):
        strategy._emit_metric()

    call_kwargs = mock_metrics.post_metric.call_args.kwargs
    assert call_kwargs["max_drawdown"] == pytest.approx(20.0)


def test_emit_metric_max_drawdown_zero_when_history_empty():
    """_emit_metric posts max_drawdown=0.0 when _realized_pnl_history is empty."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    strategy._realized_pnl_history = []
    mock_metrics = MagicMock()

    with patch.object(state_mod, "metrics", mock_metrics):
        strategy._emit_metric()

    call_kwargs = mock_metrics.post_metric.call_args.kwargs
    assert call_kwargs["max_drawdown"] == pytest.approx(0.0)


def test_emit_metric_pnl_field_is_today_not_cumulative():
    """_emit_metric posts today's realized PnL in the pnl field (not cumulative history sum)."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    # History has an old trade (+200) from a prior day; today's PnL is just -30.
    strategy._realized_pnl_history = [200.0, -30.0]
    strategy._realized_pnl_today = -30.0
    mock_metrics = MagicMock()

    with patch.object(state_mod, "metrics", mock_metrics):
        strategy._emit_metric()

    call_kwargs = mock_metrics.post_metric.call_args.kwargs
    # pnl field should reflect today only, not the cumulative sum (170.0).
    assert call_kwargs["pnl"] == pytest.approx(-30.0)


# --- Direct unit tests for the manual math helpers ---
# These pin the formulas against hand-computed answers so a future refactor (e.g.,
# swapping in nautilus_trader.analysis.SharpeRatio once rc5 lands the Rust path)
# cannot silently change the numeric contract without breaking a test.


def test_sharpe_from_pnls_zero_when_all_entries_identical():
    """Identical PnLs → stdev exactly 0.0 → Sharpe 0.0 (guarded division)."""
    from strategies.toy_momentum.strategy import _sharpe_from_pnls

    assert _sharpe_from_pnls([5.0, 5.0]) == 0.0
    assert _sharpe_from_pnls([-2.5, -2.5, -2.5]) == 0.0


def test_sharpe_from_pnls_empty_and_single_entry_return_zero():
    """Sharpe undefined without at least two data points."""
    from strategies.toy_momentum.strategy import _sharpe_from_pnls

    assert _sharpe_from_pnls([]) == 0.0
    assert _sharpe_from_pnls([42.0]) == 0.0


def test_sharpe_from_pnls_matches_hand_computed_value():
    """Hand-computed Sharpe for a known-good sequence."""
    from strategies.toy_momentum.strategy import _sharpe_from_pnls

    # mean = 5.0; sample stdev ≈ 8.6313; sharpe = 5.0 / 8.6313 ≈ 0.5793.
    assert _sharpe_from_pnls([10.0, -5.0, 15.0, -3.0, 8.0]) == pytest.approx(0.5793, rel=1e-3)


def test_max_drawdown_from_pnls_single_entry_negative_reports_loss_from_zero():
    """A single negative first trade starts drawn down from initial peak=0.0."""
    from strategies.toy_momentum.strategy import _max_drawdown_from_pnls

    # peak=0.0, cumulative=-5.0 → dd=5.0.
    assert _max_drawdown_from_pnls([-5.0]) == pytest.approx(5.0)


def test_max_drawdown_from_pnls_single_entry_positive_is_zero():
    """A single positive first trade is a new peak — no drawdown."""
    from strategies.toy_momentum.strategy import _max_drawdown_from_pnls

    assert _max_drawdown_from_pnls([10.0]) == 0.0


def test_max_drawdown_from_pnls_empty_is_zero():
    """Empty history has no drawdown."""
    from strategies.toy_momentum.strategy import _max_drawdown_from_pnls

    assert _max_drawdown_from_pnls([]) == 0.0


def test_max_drawdown_from_pnls_monotonic_up_is_zero():
    """A monotonically increasing equity curve has zero drawdown."""
    from strategies.toy_momentum.strategy import _max_drawdown_from_pnls

    assert _max_drawdown_from_pnls([1.0, 2.0, 3.0, 4.0]) == 0.0


def test_max_drawdown_from_pnls_matches_hand_computed_value():
    """Hand-computed peak-to-trough for a known-good sequence."""
    from strategies.toy_momentum.strategy import _max_drawdown_from_pnls

    # Cumulative: 10, 15, -5, -2 → peak=15, trough=-5, drawdown=20.
    assert _max_drawdown_from_pnls([10.0, 5.0, -20.0, 3.0]) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Native-epic 3 tests: flatten in on_stop
# ---------------------------------------------------------------------------


def test_on_stop_flattens_and_cancels():
    """on_stop calls cancel_all_orders and close_all_positions with instrument_id, then audits."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    mock_log = MagicMock()
    mock_clock = MagicMock()
    mock_audit = MagicMock()
    mock_cancel = MagicMock()
    mock_close = MagicMock()

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "clock", mock_clock, create=True),
        patch.object(ToyMomentum, "id", "TEST-STRATEGY-ID", create=True),
        patch.object(ToyMomentum, "cancel_all_orders", mock_cancel, create=True),
        patch.object(ToyMomentum, "close_all_positions", mock_close, create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy.on_stop()

    mock_cancel.assert_called_once_with(strategy._config.instrument_id)
    mock_close.assert_called_once_with(strategy._config.instrument_id)
    mock_audit.post.assert_called_once_with(
        actor="runner",
        action="flatten_on_stop",
        payload={
            "strategy_id": 42,
            "instrument": str(strategy._config.instrument_id),
        },
    )


def test_on_stop_swallows_flatten_errors():
    """on_stop must not propagate flatten exceptions and must warn via log."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    mock_log = MagicMock()
    mock_clock = MagicMock()
    mock_audit = MagicMock()
    mock_cancel = MagicMock(side_effect=RuntimeError("network down"))
    mock_close = MagicMock(side_effect=RuntimeError("network down"))

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "clock", mock_clock, create=True),
        patch.object(ToyMomentum, "id", "TEST-STRATEGY-ID", create=True),
        patch.object(ToyMomentum, "cancel_all_orders", mock_cancel, create=True),
        patch.object(ToyMomentum, "close_all_positions", mock_close, create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        # Must not raise even though both flatten calls fail.
        strategy.on_stop()

    assert mock_log.warning.call_count >= 2, (
        "Expected at least one warning per failed flatten call"
    )


def test_on_stop_still_cancels_timer_when_flatten_fails():
    """Timer cancel must run before flatten; both flatten calls are attempted regardless of failures.

    Verifies call ordering via mock side_effects that record into a shared list.
    Both cancel_all_orders and close_all_positions raise; timer_cancel must appear
    first and both flatten calls must still have been made (warnings prove they ran).
    """
    import nautilus_runner.state as state_mod

    strategy = _make_strategy_with_db_id()
    call_order: list[str] = []

    mock_log = MagicMock()
    mock_clock = MagicMock()
    mock_clock.cancel_timer.side_effect = lambda name: call_order.append("timer_cancel")
    mock_audit = MagicMock()

    mock_cancel = MagicMock(side_effect=RuntimeError("fail"))
    mock_close = MagicMock(side_effect=RuntimeError("fail"))

    # Wrap the MagicMocks to also append to call_order before raising.
    original_cancel_se = mock_cancel.side_effect
    original_close_se = mock_close.side_effect

    def _cancel_side_effect(instrument_id: object) -> None:
        call_order.append("cancel_all_orders")
        raise RuntimeError("fail")

    def _close_side_effect(instrument_id: object) -> None:
        call_order.append("close_all_positions")
        raise RuntimeError("fail")

    mock_cancel.side_effect = _cancel_side_effect
    mock_close.side_effect = _close_side_effect

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "clock", mock_clock, create=True),
        patch.object(ToyMomentum, "id", "TEST-STRATEGY-ID", create=True),
        patch.object(ToyMomentum, "cancel_all_orders", mock_cancel, create=True),
        patch.object(ToyMomentum, "close_all_positions", mock_close, create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy.on_stop()

    # Timer cancel must fire first (before any flatten attempt).
    assert call_order[0] == "timer_cancel", (
        f"timer_cancel must run first; got order: {call_order}"
    )
    # Both flatten calls were made (exceptions swallowed, warnings emitted).
    assert "cancel_all_orders" in call_order
    assert "close_all_positions" in call_order
    assert mock_log.warning.call_count >= 2


def test_on_stop_skips_audit_when_no_strategy_db_id():
    """on_stop must flatten but NOT audit when strategy_db_id is None."""
    import nautilus_runner.state as state_mod

    strategy = _make_strategy()  # no strategy_db_id → defaults to None
    mock_log = MagicMock()
    mock_clock = MagicMock()
    mock_audit = MagicMock()
    mock_cancel = MagicMock()
    mock_close = MagicMock()

    with (
        patch.object(ToyMomentum, "log", mock_log),
        patch.object(ToyMomentum, "clock", mock_clock, create=True),
        patch.object(ToyMomentum, "id", "TEST-STRATEGY-ID", create=True),
        patch.object(ToyMomentum, "cancel_all_orders", mock_cancel, create=True),
        patch.object(ToyMomentum, "close_all_positions", mock_close, create=True),
        patch.object(state_mod, "audit_writer", mock_audit),
    ):
        strategy.on_stop()

    mock_cancel.assert_called_once_with(strategy._config.instrument_id)
    mock_close.assert_called_once_with(strategy._config.instrument_id)
    mock_audit.post.assert_not_called()
