"""Integration tests for ``nautilus_runner.backtest.runner.run_backtest``.

Uses a hand-built synthetic Parquet catalog in ``tmp_path`` and drives the
real ``BacktestEngine`` end-to-end.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Price, Quantity
from nautilus_trader.trading import Strategy

from nautilus_runner.backtest.runner import realized_pnls_from_report, run_backtest
from nautilus_runner.data.catalog import write_bars_to_catalog

BAR_TYPE = "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
INSTRUMENT_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
HOUR_NS = 60 * 60 * 1_000_000_000


def _write_trending_catalog(path: Path, n_hours: int, start: datetime, base_price: float = 50000.0) -> None:
    bt = BarType.from_str(BAR_TYPE)
    start_ns = int(start.timestamp() * 1_000_000_000)
    bars = []
    for i in range(n_hours):
        price = base_price + i * 10  # gentle uptrend, deterministic
        p = Price(price, 2)
        ts_open = start_ns + i * HOUR_NS
        bars.append(Bar(bt, p, p, p, p, Quantity(1.0, 3), ts_open, ts_open + HOUR_NS - 1))
    write_bars_to_catalog(path, bars)


_BT = BarType.from_str(BAR_TYPE)


class _NoopStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__(StrategyConfig())

    def on_start(self) -> None:
        self.subscribe_bars(_BT)

    def on_bar(self, bar: Bar) -> None:  # noqa: D401 - trivial no-op
        pass


class _BuyOnceStrategy(Strategy):
    """Places one market buy on the first bar, closes on the second — guarantees exactly one trade."""

    def __init__(self) -> None:
        super().__init__(StrategyConfig())
        self._bars_seen = 0

    def on_start(self) -> None:
        self.subscribe_bars(_BT)

    def on_bar(self, bar: Bar) -> None:
        self._bars_seen += 1
        if self._bars_seen == 1:
            order = self.order_factory.market(
                instrument_id=INSTRUMENT_ID,
                order_side=OrderSide.BUY,
                quantity=Quantity(0.01, 3),
            )
            self.submit_order(order)
        elif self._bars_seen == 2:
            self.close_all_positions(INSTRUMENT_ID)


def test_run_backtest_refuses_empty_window(tmp_path: Path):
    _write_trending_catalog(tmp_path, n_hours=24, start=datetime(2025, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="no bars"):
        run_backtest(
            catalog_path=tmp_path,
            bar_type=BAR_TYPE,
            strategy=_NoopStrategy(),
            start=datetime(2030, 1, 1, tzinfo=timezone.utc),
            end=datetime(2030, 1, 2, tzinfo=timezone.utc),
        )


def test_run_backtest_rejects_naive_datetime(tmp_path: Path):
    _write_trending_catalog(tmp_path, n_hours=24, start=datetime(2025, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="tz-aware"):
        run_backtest(
            catalog_path=tmp_path,
            bar_type=BAR_TYPE,
            strategy=_NoopStrategy(),
            start=datetime(2025, 1, 1),  # naive
            end=datetime(2025, 1, 2, tzinfo=timezone.utc),
        )


def test_run_backtest_noop_strategy_zero_trades(tmp_path: Path):
    """The engine drives 24 bars through a strategy that never trades. Balance unchanged, zero trades."""
    _write_trending_catalog(tmp_path, n_hours=24, start=datetime(2025, 1, 1, tzinfo=timezone.utc))
    summary = run_backtest(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy=_NoopStrategy(),
        start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    assert summary.n_bars == 24
    assert summary.n_trades == 0
    assert summary.realized_pnl_total == Decimal("0.0")
    assert summary.final_balance == Decimal("10000.0")
    assert summary.sharpe == 0.0
    assert summary.max_drawdown == 0.0
    assert summary.raw_stats["total_orders"] == 0
    assert summary.raw_stats["total_positions"] == 0


def test_run_backtest_records_at_least_one_trade(tmp_path: Path):
    """Buy-then-close strategy: engine should record exactly one position closing."""
    _write_trending_catalog(tmp_path, n_hours=24, start=datetime(2025, 1, 1, tzinfo=timezone.utc))
    summary = run_backtest(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy=_BuyOnceStrategy(),
        start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    assert summary.n_trades >= 1
    assert summary.raw_stats["total_orders"] >= 2  # open + close
    # Bar-execution fills at the next bar's open, so the round-trip is essentially
    # flat on price and slightly negative from the 18 bps taker fee (paid twice).
    # Final balance moves with realized PnL; both must agree to within cents.
    delta = summary.final_balance - summary.initial_balance
    assert abs(delta - summary.realized_pnl_total) < Decimal("0.01")


def test_realized_pnls_from_report_parses_money_strings():
    """Nautilus formats realized_pnl as ``"<amount> <CURRENCY>"``. Verify parse."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "realized_pnl": [
                "-0.08001800 USDT",
                "1.06101508 USDT",
                "0.00000000 USDT",
                None,
                "bogus USDT",  # unparseable numeric part
            ],
        },
    )
    out = realized_pnls_from_report(df)
    assert out == [-0.08001800, 1.06101508, 0.0]


def test_realized_pnls_from_report_handles_empty_and_missing_column():
    import pandas as pd

    assert realized_pnls_from_report(None) == []
    assert realized_pnls_from_report(pd.DataFrame()) == []
    assert realized_pnls_from_report(pd.DataFrame({"other": [1]})) == []


def test_run_backtest_summary_serializes_to_dict(tmp_path: Path):
    _write_trending_catalog(tmp_path, n_hours=24, start=datetime(2025, 1, 1, tzinfo=timezone.utc))
    summary = run_backtest(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy=_NoopStrategy(),
        start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    d = summary.to_dict()
    assert d["bar_type"] == BAR_TYPE
    assert d["start"] == "2025-01-01T00:00:00+00:00"
    assert d["end"] == "2025-01-02T00:00:00+00:00"
    assert d["initial_balance"] == "10000"
    assert isinstance(d["raw_stats"]["stats_pnls"], dict)
