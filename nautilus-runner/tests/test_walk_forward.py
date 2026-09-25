"""Unit + integration tests for the walk-forward evaluator."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.model import Bar, BarType, Price, Quantity

from nautilus_runner.backtest.strategies import BuyAndHold, BuyAndHoldConfig
from nautilus_runner.backtest.walk_forward import (
    add_months,
    iter_windows,
    walk_forward,
)
from nautilus_runner.data.catalog import write_bars_to_catalog

BAR_TYPE = "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
HOUR_NS = 60 * 60 * 1_000_000_000
UTC = timezone.utc


# ---------------------------------------------------------------------------
# add_months
# ---------------------------------------------------------------------------


def test_add_months_forward_within_year():
    got = add_months(datetime(2025, 1, 1, tzinfo=UTC), 3)
    assert got == datetime(2025, 4, 1, tzinfo=UTC)


def test_add_months_crosses_year_boundary():
    got = add_months(datetime(2025, 11, 1, tzinfo=UTC), 4)
    assert got == datetime(2026, 3, 1, tzinfo=UTC)


def test_add_months_multi_year_span():
    got = add_months(datetime(2025, 6, 1, tzinfo=UTC), 30)
    assert got == datetime(2027, 12, 1, tzinfo=UTC)


def test_add_months_raises_on_day_overflow():
    # Jan 31 + 1 month → Feb 31 does not exist. Fail loudly.
    with pytest.raises(ValueError):
        add_months(datetime(2025, 1, 31, tzinfo=UTC), 1)


# ---------------------------------------------------------------------------
# iter_windows
# ---------------------------------------------------------------------------


def test_iter_windows_produces_rolling_train_test_pairs():
    got = list(
        iter_windows(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 7, 1, tzinfo=UTC),
            train_months=3,
            test_months=1,
            step_months=1,
        )
    )
    # window 0: train Jan-Mar, test Apr → test_end May
    # window 1: train Feb-Apr, test May → test_end Jun
    # window 2: train Mar-May, test Jun → test_end Jul (== end, still yielded)
    # window 3: train Apr-Jun, test Jul → test_end Aug (> end, drop)
    assert len(got) == 3
    assert got[0] == (
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 4, 1, tzinfo=UTC),
        datetime(2025, 4, 1, tzinfo=UTC),
        datetime(2025, 5, 1, tzinfo=UTC),
    )
    assert got[-1] == (
        datetime(2025, 3, 1, tzinfo=UTC),
        datetime(2025, 6, 1, tzinfo=UTC),
        datetime(2025, 6, 1, tzinfo=UTC),
        datetime(2025, 7, 1, tzinfo=UTC),
    )


def test_iter_windows_empty_when_range_too_small():
    got = list(
        iter_windows(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 2, 1, tzinfo=UTC),
            train_months=3,
            test_months=1,
            step_months=1,
        )
    )
    assert got == []


def test_iter_windows_test_end_boundary_is_inclusive_yield():
    # A window whose test_end exactly equals `end` MUST yield.
    got = list(
        iter_windows(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 4, 1, tzinfo=UTC),
            train_months=2,
            test_months=1,
            step_months=1,
        )
    )
    assert len(got) == 1
    assert got[0][3] == datetime(2025, 4, 1, tzinfo=UTC)


def test_iter_windows_step_larger_than_test_skips_forward():
    got = list(
        iter_windows(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, tzinfo=UTC),
            train_months=3,
            test_months=1,
            step_months=3,
        )
    )
    # test starts: Apr, Jul, Oct → 3 windows (Nov test_end also fits: no, Oct+1=Nov <= Jan 2026 yes)
    # Let's enumerate:
    # w0: train=Jan..Apr, test=Apr..May
    # w1: train=Apr..Jul, test=Jul..Aug
    # w2: train=Jul..Oct, test=Oct..Nov
    # w3: train=Oct..Jan(2026), test=Jan(2026)..Feb(2026) → test_end > end, drop
    assert len(got) == 3
    assert [w[0].month for w in got] == [1, 4, 7]


def test_iter_windows_rejects_bad_month_values():
    with pytest.raises(ValueError):
        list(
            iter_windows(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 4, 1, tzinfo=UTC),
                train_months=0,
                test_months=1,
                step_months=1,
            )
        )


def test_iter_windows_rejects_reversed_range():
    with pytest.raises(ValueError):
        list(
            iter_windows(
                start=datetime(2025, 4, 1, tzinfo=UTC),
                end=datetime(2025, 1, 1, tzinfo=UTC),
                train_months=1,
                test_months=1,
                step_months=1,
            )
        )


# ---------------------------------------------------------------------------
# walk_forward integration (small synthetic catalog)
# ---------------------------------------------------------------------------


def _write_synthetic_catalog(path: Path, start: datetime, n_hours: int) -> None:
    bt = BarType.from_str(BAR_TYPE)
    start_ns = int(start.timestamp() * 1_000_000_000)
    bars = []
    for i in range(n_hours):
        price = 50000.00 + 300 * math.sin(i / 12)
        p = Price(price, 2)
        ts_open = start_ns + i * HOUR_NS
        bars.append(Bar(bt, p, p, p, p, Quantity(1.0, 3), ts_open, ts_open + HOUR_NS - 1))
    write_bars_to_catalog(path, bars)


def _buy_and_hold_factory(bar_type_str: str):
    def _make():
        return BuyAndHold(
            BuyAndHoldConfig(
                instrument_id=BarType.from_str(bar_type_str).instrument_id,
                bar_type=BarType.from_str(bar_type_str),
                trade_size=Decimal("0.001"),
            )
        )

    return _make


def test_walk_forward_produces_windows_with_train_and_test_summaries(tmp_path: Path):
    # 3 months of hourly synthetic bars → 3 walk-forward windows at 2m train / 1m test / 1m step
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 4)  # generous, covers past end

    result = walk_forward(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_factory=_buy_and_hold_factory(BAR_TYPE),
        start=start,
        end=datetime(2025, 5, 1, tzinfo=UTC),
        train_months=2,
        test_months=1,
        step_months=1,
    )
    # windows:
    # w0: train Jan..Mar, test Mar..Apr
    # w1: train Feb..Apr, test Apr..May → test_end==end, still yielded
    assert len(result.windows) == 2
    for w in result.windows:
        assert w.train_end == w.test_start
        assert w.train_summary.n_bars > 0
        assert w.test_summary.n_bars > 0
        assert w.train_summary.raw_stats["total_orders"] >= 1
        assert w.test_summary.raw_stats["total_orders"] >= 1


def test_walk_forward_empty_result_serializes_to_dict(tmp_path: Path):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 40)

    result = walk_forward(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_factory=_buy_and_hold_factory(BAR_TYPE),
        start=start,
        end=datetime(2025, 2, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
    )
    assert result.windows == []  # 1m train + 1m test needs 2m of range; only 1m given
    d = result.to_dict()
    assert d == {"n_windows": 0, "windows": []}


def test_walk_forward_non_empty_result_serializes_to_dict(tmp_path: Path):
    """Non-empty ``to_dict()`` must produce ISO strings + stringified Decimals."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    result = walk_forward(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_factory=_buy_and_hold_factory(BAR_TYPE),
        start=start,
        end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
    )
    assert len(result.windows) == 1
    d = result.to_dict()
    assert d["n_windows"] == 1
    w0 = d["windows"][0]
    assert w0["train_start"] == "2025-01-01T00:00:00+00:00"
    assert w0["train_end"] == "2025-02-01T00:00:00+00:00"
    assert w0["test_start"] == "2025-02-01T00:00:00+00:00"
    assert w0["test_end"] == "2025-03-01T00:00:00+00:00"
    # BacktestSummary serialization: Decimals rendered as strings, not floats.
    assert isinstance(w0["train_summary"]["initial_balance"], str)
    assert isinstance(w0["test_summary"]["realized_pnl_total"], str)


def test_walk_forward_calls_factory_twice_per_window(tmp_path: Path):
    """Fresh-instance-per-run contract: factory called ``2 * n_windows`` times with distinct returns."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 4)

    base_factory = _buy_and_hold_factory(BAR_TYPE)
    instances: list[object] = []

    def tracking_factory():
        s = base_factory()
        instances.append(s)
        return s

    result = walk_forward(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_factory=tracking_factory,
        start=start,
        end=datetime(2025, 4, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
    )
    # windows: (Jan..Feb, Feb..Mar), (Feb..Mar, Mar..Apr) → 2 windows × 2 runs = 4
    assert len(result.windows) == 2
    assert len(instances) == 2 * len(result.windows)
    # All returned instances must be distinct objects — a regression that reused
    # one instance across the two runs would violate Nautilus's no-re-attach rule.
    # The ``instances`` list keeps every reference live for the duration of this
    # assertion, so ``id()`` cannot be reused via garbage collection.
    assert len({id(s) for s in instances}) == len(instances)
