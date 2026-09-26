"""Unit + integration tests for the parameter-search grid wrapper."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.model import Bar, BarType, InstrumentId, Price, Quantity

from nautilus_runner.backtest.param_search import (
    ParamSearchResult,
    iter_grid,
    walk_forward_search,
)
from nautilus_runner.backtest.strategies import BuyAndHold, BuyAndHoldConfig
from nautilus_runner.data.catalog import write_bars_to_catalog

BAR_TYPE = "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
HOUR_NS = 60 * 60 * 1_000_000_000
UTC = timezone.utc


# ---------------------------------------------------------------------------
# iter_grid
# ---------------------------------------------------------------------------


def test_iter_grid_empty_grid_yields_nothing():
    assert list(iter_grid({})) == []


def test_iter_grid_single_param_enumerates_values():
    got = list(iter_grid({"fast": [3, 5, 10]}))
    assert got == [{"fast": 3}, {"fast": 5}, {"fast": 10}]


def test_iter_grid_cartesian_product_is_deterministic():
    got = list(iter_grid({"slow": [20, 50], "fast": [3, 5]}))
    # Keys sorted alphabetically → 'fast' before 'slow' in the outer loop.
    assert got == [
        {"fast": 3, "slow": 20},
        {"fast": 3, "slow": 50},
        {"fast": 5, "slow": 20},
        {"fast": 5, "slow": 50},
    ]


def test_iter_grid_rejects_empty_value_sequence():
    with pytest.raises(ValueError, match="empty"):
        list(iter_grid({"fast": [3, 5], "slow": []}))


# ---------------------------------------------------------------------------
# walk_forward_search integration
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


def _buy_and_hold_from_params(params: dict) -> BuyAndHold:
    """A BuyAndHold factory that reads ``trade_size`` from the param combo.

    BuyAndHold does not naturally have a hyperparameter grid, but ``trade_size``
    lets the integration test exercise multiple combos without pulling
    ToyMomentum's live-only wiring into the test path.
    """
    return BuyAndHold(
        BuyAndHoldConfig(
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
            bar_type=BarType.from_str(BAR_TYPE),
            trade_size=Decimal(str(params["trade_size"])),
            size_precision=3,
        )
    )


def test_walk_forward_search_returns_one_window_result_per_walk_window(tmp_path: Path):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 4)

    result = walk_forward_search(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start,
        end=datetime(2025, 4, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
        param_grid={"trade_size": ["0.001", "0.002"]},
    )
    # walk_forward yields 2 windows for this range/step; each window keeps ALL combos.
    assert len(result.windows) == 2
    for w in result.windows:
        assert len(w.all_train_summaries) == 2
        assert len(w.all_test_summaries) == 2
        assert w.winner_params in [p for p, _ in w.all_train_summaries]


def test_walk_forward_search_picks_highest_train_sharpe_as_winner(tmp_path: Path):
    """Winner selection uses ``sharpe_from_pnls`` on the train summary by default."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    result = walk_forward_search(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start,
        end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
        param_grid={"trade_size": ["0.001", "0.003"]},
    )
    assert len(result.windows) == 1
    w = result.windows[0]
    train_scores = {tuple(p.items()): s.sharpe for p, s in w.all_train_summaries}
    winner_key = tuple(w.winner_params.items())
    assert train_scores[winner_key] == max(train_scores.values())


def test_walk_forward_search_custom_selector_picks_lowest_drawdown(tmp_path: Path):
    """Passing a custom selector overrides the default train-Sharpe criterion."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    result = walk_forward_search(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start,
        end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
        param_grid={"trade_size": ["0.001", "0.003"]},
        selector=lambda s: -s.max_drawdown,  # smallest drawdown → highest score
    )
    w = result.windows[0]
    train_dds = {tuple(p.items()): s.max_drawdown for p, s in w.all_train_summaries}
    winner_key = tuple(w.winner_params.items())
    assert train_dds[winner_key] == min(train_dds.values())


def test_walk_forward_search_calls_factory_2WP_times(tmp_path: Path):
    """Fresh-instance-per-run × walk_forward × combo count.

    For P combos and W windows, ``strategy_from_params`` should be called
    ``2 * W * P`` times (once for each train + test run of each combo).
    """
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 4)

    calls: list[dict] = []

    def tracking_factory(params: dict) -> BuyAndHold:
        calls.append(dict(params))
        return _buy_and_hold_from_params(params)

    grid = {"trade_size": ["0.001", "0.002"]}
    result = walk_forward_search(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_from_params=tracking_factory,
        start=start,
        end=datetime(2025, 4, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
        param_grid=grid,
    )
    n_windows = len(result.windows)
    n_combos = 2
    assert len(calls) == 2 * n_windows * n_combos


def test_walk_forward_search_empty_result_from_range_too_small_serializes(tmp_path: Path):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 40)

    result = walk_forward_search(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start,
        end=datetime(2025, 2, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
        param_grid={"trade_size": ["0.001"]},
    )
    assert result.windows == []
    assert result.to_dict() == {"n_windows": 0, "windows": []}


def test_walk_forward_search_non_empty_result_serializes_to_dict(tmp_path: Path):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    result = walk_forward_search(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start,
        end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
        param_grid={"trade_size": ["0.001", "0.002"]},
    )
    d = result.to_dict()
    assert d["n_windows"] == 1
    w = d["windows"][0]
    assert w["train_start"] == "2025-01-01T00:00:00+00:00"
    assert w["test_end"] == "2025-03-01T00:00:00+00:00"
    assert isinstance(w["winner_params"], dict) and "trade_size" in w["winner_params"]
    assert len(w["all_train_summaries"]) == 2
    assert isinstance(w["winner_train_summary"]["initial_balance"], str)


def test_walk_forward_search_rejects_empty_grid(tmp_path: Path):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    with pytest.raises(ValueError, match="zero combinations"):
        walk_forward_search(
            catalog_path=tmp_path,
            bar_type=BAR_TYPE,
            strategy_from_params=_buy_and_hold_from_params,
            start=start,
            end=datetime(2025, 3, 1, tzinfo=UTC),
            train_months=1,
            test_months=1,
            step_months=1,
            param_grid={},
        )


def test_walk_forward_search_type_alias_exports():
    """ParamSearchResult is importable at the module level for downstream code."""
    assert ParamSearchResult().to_dict() == {"n_windows": 0, "windows": []}
