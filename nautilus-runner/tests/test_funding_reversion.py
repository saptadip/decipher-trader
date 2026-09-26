"""Unit tests for FundingReversion signal logic.

Uses synthetic funding events + hand-driven bars via ``strategy.on_bar()`` so
each test isolates a specific branch. Integration against ``BacktestEngine``
lives in ``test_backtest_runner.py``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Price, Quantity

from strategies.funding_reversion.strategy import (
    FundingReversion,
    FundingReversionConfig,
)

INSTRUMENT = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL")
MINUTE_NS = 60 * 1_000_000_000
HOUR_NS = 60 * MINUTE_NS


def _bar(ts_ns: int, close: float = 50000.0) -> Bar:
    p = Price(close, 2)
    return Bar(BAR_TYPE, p, p, p, p, Quantity(1.0, 3), ts_ns, ts_ns + HOUR_NS - 1)


def _fund(ts_ns: int, rate: float) -> dict:
    return {"ts_ns": ts_ns, "funding_rate": rate, "mark_price": 50000.0, "symbol": "BTCUSDT"}


def _make(
    funding_events: list[dict],
    *,
    entry_threshold: float = 0.001,
    exit_threshold: float = 0.0001,
    trade_size: Decimal = Decimal("0.001"),
    max_notional: float = 10_000.0,
    max_daily_loss: float = 1000.0,
    max_position: float = 0.010,
) -> FundingReversion:
    cfg = FundingReversionConfig(
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,
        funding_events=funding_events,
        entry_threshold=entry_threshold,
        exit_threshold=exit_threshold,
        trade_size=trade_size,
        max_notional=max_notional,
        max_daily_loss=max_daily_loss,
        max_position=max_position,
    )
    return FundingReversion(cfg)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_config_rejects_non_positive_entry_threshold():
    with pytest.raises(ValueError, match="entry_threshold"):
        FundingReversionConfig(
            instrument_id=INSTRUMENT, bar_type=BAR_TYPE, funding_events=[],
            entry_threshold=0.0, exit_threshold=0.0, trade_size=Decimal("0.001"),
            max_notional=1000, max_daily_loss=100, max_position=1,
        )


def test_config_rejects_negative_exit_threshold():
    with pytest.raises(ValueError, match="exit_threshold"):
        FundingReversionConfig(
            instrument_id=INSTRUMENT, bar_type=BAR_TYPE, funding_events=[],
            entry_threshold=0.001, exit_threshold=-0.0001, trade_size=Decimal("0.001"),
            max_notional=1000, max_daily_loss=100, max_position=1,
        )


def test_config_rejects_exit_ge_entry():
    with pytest.raises(ValueError, match="strictly less"):
        FundingReversionConfig(
            instrument_id=INSTRUMENT, bar_type=BAR_TYPE, funding_events=[],
            entry_threshold=0.001, exit_threshold=0.001, trade_size=Decimal("0.001"),
            max_notional=1000, max_daily_loss=100, max_position=1,
        )


# ---------------------------------------------------------------------------
# Signal — entry
# ---------------------------------------------------------------------------


def test_deeply_negative_funding_triggers_buy_when_flat():
    """Negative funding = shorts pay longs = long-side edge → BUY."""
    events = [_fund(1 * HOUR_NS, rate=-0.002)]
    s = _make(events, entry_threshold=0.001)
    mock_of = MagicMock()
    mock_of.market.return_value = MagicMock()
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", mock_of),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
    assert mock_submit.call_count == 1
    assert mock_of.market.call_args.kwargs["order_side"] == OrderSide.BUY
    assert s._signed_position == pytest.approx(0.001)


def test_deeply_positive_funding_triggers_sell_when_flat():
    """Positive funding = longs pay shorts = short-side edge → SELL."""
    events = [_fund(1 * HOUR_NS, rate=+0.002)]
    s = _make(events, entry_threshold=0.001)
    mock_of = MagicMock()
    mock_of.market.return_value = MagicMock()
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", mock_of),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
    assert mock_submit.call_count == 1
    assert mock_of.market.call_args.kwargs["order_side"] == OrderSide.SELL
    assert s._signed_position == pytest.approx(-0.001)


def test_sub_threshold_funding_does_not_trigger_entry():
    events = [_fund(1 * HOUR_NS, rate=+0.0005)]
    s = _make(events, entry_threshold=0.001)
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
    assert mock_submit.call_count == 0
    assert s._signed_position == 0.0


# ---------------------------------------------------------------------------
# Signal — exit
# ---------------------------------------------------------------------------


def test_mean_reversion_closes_open_long():
    events = [
        _fund(1 * HOUR_NS, rate=-0.002),   # enter long
        _fund(9 * HOUR_NS, rate=+0.00005),  # revert to |0.00005| < exit_threshold 0.0001
    ]
    s = _make(events, entry_threshold=0.001, exit_threshold=0.0001)
    mock_of = MagicMock()
    mock_of.market.return_value = MagicMock()
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", mock_of),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
        s.on_bar(_bar(9 * HOUR_NS))
    # BUY (enter), then SELL (exit)
    assert mock_submit.call_count == 2
    sides = [call.kwargs["order_side"] for call in mock_of.market.call_args_list]
    assert sides == [OrderSide.BUY, OrderSide.SELL]
    assert s._signed_position == pytest.approx(0.0)


def test_mean_reversion_closes_open_short():
    events = [
        _fund(1 * HOUR_NS, rate=+0.002),   # enter short
        _fund(9 * HOUR_NS, rate=-0.00005),  # revert
    ]
    s = _make(events)
    mock_of = MagicMock()
    mock_of.market.return_value = MagicMock()
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", mock_of),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
        s.on_bar(_bar(9 * HOUR_NS))
    sides = [call.kwargs["order_side"] for call in mock_of.market.call_args_list]
    assert sides == [OrderSide.SELL, OrderSide.BUY]
    assert s._signed_position == pytest.approx(0.0)


def test_still_extreme_funding_does_not_close_position():
    events = [
        _fund(1 * HOUR_NS, rate=-0.002),   # enter long
        _fund(9 * HOUR_NS, rate=-0.0015),  # still extreme, do NOT close
    ]
    s = _make(events, entry_threshold=0.001, exit_threshold=0.0001)
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
        s.on_bar(_bar(9 * HOUR_NS))
    assert mock_submit.call_count == 1  # only the entry
    assert s._signed_position == pytest.approx(0.001)


def test_no_pyramiding_ignores_repeat_entry_signal():
    """Two consecutive extreme events on the same side must not stack the position."""
    events = [
        _fund(1 * HOUR_NS, rate=-0.002),
        _fund(9 * HOUR_NS, rate=-0.003),  # still deeply negative but we're already long
    ]
    s = _make(events)
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
        s.on_bar(_bar(9 * HOUR_NS))
    assert mock_submit.call_count == 1


# ---------------------------------------------------------------------------
# Position caps
# ---------------------------------------------------------------------------


def test_max_position_cap_blocks_entry_when_would_exceed():
    events = [_fund(1 * HOUR_NS, rate=-0.002)]
    s = _make(events, trade_size=Decimal("0.005"), max_position=0.001)  # entry > cap
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
    assert mock_submit.call_count == 0
    assert s._signed_position == 0.0


def test_max_notional_cap_blocks_entry_when_would_exceed():
    events = [_fund(1 * HOUR_NS, rate=-0.002)]
    # 0.01 BTC @ $50,000 = $500 notional > max_notional 100
    s = _make(events, trade_size=Decimal("0.01"), max_position=1.0, max_notional=100.0)
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
    assert mock_submit.call_count == 0


# ---------------------------------------------------------------------------
# Event consumption ordering
# ---------------------------------------------------------------------------


def test_events_consumed_only_when_ts_le_bar_ts():
    """A funding event beyond the current bar's ts must NOT fire yet."""
    events = [
        _fund(9 * HOUR_NS, rate=-0.002),  # in future relative to bar at hour 1
    ]
    s = _make(events, entry_threshold=0.001)
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(1 * HOUR_NS))
    assert mock_submit.call_count == 0
    # Then when the bar reaches hour 9, the event fires.
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(9 * HOUR_NS))
    assert mock_submit.call_count == 1


def test_multiple_events_in_single_bar_all_processed():
    """If several funding events fall between bars, all fire on the next on_bar."""
    events = [
        _fund(1 * HOUR_NS, rate=-0.002),   # BUY
        _fund(2 * HOUR_NS, rate=+0.00005),  # revert → SELL to close
    ]
    s = _make(events, entry_threshold=0.001, exit_threshold=0.0001)
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(3 * HOUR_NS))  # single bar covers both events
    assert mock_submit.call_count == 2  # entry + exit
    assert s._signed_position == pytest.approx(0.0)


def test_unsorted_input_events_sorted_at_construction():
    """Config may pass events in any order; strategy must process chronologically."""
    events = [
        _fund(9 * HOUR_NS, rate=+0.00005),  # later, would revert
        _fund(1 * HOUR_NS, rate=-0.002),    # earlier, would enter
    ]
    s = _make(events)
    mock_submit = MagicMock()
    with (
        patch.object(FundingReversion, "log", MagicMock()),
        patch.object(FundingReversion, "order_factory", MagicMock()),
        patch.object(FundingReversion, "submit_order", mock_submit),
    ):
        s.on_bar(_bar(15 * HOUR_NS))
    # BUY at t=1 then SELL at t=9 (chronological)
    assert mock_submit.call_count == 2
    assert s._signed_position == pytest.approx(0.0)
