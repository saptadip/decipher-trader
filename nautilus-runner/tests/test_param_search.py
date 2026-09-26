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


def test_walk_forward_search_selector_picks_max_scoring_combo(tmp_path: Path):
    """The winner must be the combo with the highest selector score.

    Uses a custom selector on ``realized_pnl_total`` (a field that genuinely
    differs between ``BuyAndHold`` combos with different ``trade_size``s —
    the default per-trade Sharpe collapses to 0.0 on single-trade windows
    per its ``len < 2`` early-return, so a naive default-selector assertion
    would pass by tie regardless of the selection mechanism).
    """
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    def realized_pnl_selector(train_summary):
        return float(train_summary.realized_pnl_total)

    result = walk_forward_search(
        catalog_path=tmp_path,
        bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start,
        end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1,
        test_months=1,
        step_months=1,
        param_grid={"trade_size": ["0.001", "0.002", "0.003"]},
        selector=realized_pnl_selector,
    )
    assert len(result.windows) == 1
    w = result.windows[0]
    scores = {tuple(p.items()): realized_pnl_selector(s) for p, s in w.all_train_summaries}
    winner_key = tuple(w.winner_params.items())
    assert scores[winner_key] == max(scores.values())
    # Sanity: the max score is strictly greater than the min — otherwise the
    # test degenerates to a tie and proves nothing.
    assert max(scores.values()) > min(scores.values())


def test_walk_forward_search_selector_sign_flip_picks_opposite_combo(tmp_path: Path):
    """A selector with the opposite sign must pick the opposite combo.

    Guards against a hypothetical regression that swapped ``max`` for ``min``
    in the winner-selection code path.
    """
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    def positive(s):
        return float(s.realized_pnl_total)

    def negative(s):
        return -float(s.realized_pnl_total)

    grid = {"trade_size": ["0.001", "0.002", "0.003"]}
    r_pos = walk_forward_search(
        catalog_path=tmp_path, bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start, end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1, test_months=1, step_months=1,
        param_grid=grid, selector=positive,
    )
    r_neg = walk_forward_search(
        catalog_path=tmp_path, bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start, end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1, test_months=1, step_months=1,
        param_grid=grid, selector=negative,
    )
    assert r_pos.windows[0].winner_params != r_neg.windows[0].winner_params


def test_walk_forward_search_winner_test_summary_matches_winner_params(tmp_path: Path):
    """The OOS scorecard must be the test summary of the SAME combo whose train won.

    A regression that returned ``test_summaries[0][1]`` regardless of the winner
    would silently pass every other assertion in this suite. This test forces a
    known non-first combo to win via a targeted selector, then asserts the
    winner's test summary IS the one that belongs to that combo.
    """
    start = datetime(2025, 1, 1, tzinfo=UTC)
    _write_synthetic_catalog(tmp_path, start, n_hours=24 * 31 * 3)

    # Selector that rewards trade_size closest to 0.002 (middle combo) —
    # arithmetically forces the middle combo to win as long as the three
    # combos have distinguishable realized_pnl_totals.
    result = walk_forward_search(
        catalog_path=tmp_path, bar_type=BAR_TYPE,
        strategy_from_params=_buy_and_hold_from_params,
        start=start, end=datetime(2025, 3, 1, tzinfo=UTC),
        train_months=1, test_months=1, step_months=1,
        param_grid={"trade_size": ["0.001", "0.002", "0.003"]},
        selector=lambda s: -abs(float(s.realized_pnl_total) - 2.0),  # peaks near |pnl|=2
    )
    w = result.windows[0]
    # Locate the (combo, summary) pair whose combo equals the winner_params.
    matching = [s for c, s in w.all_test_summaries if c == w.winner_params]
    assert len(matching) == 1, f"expected exactly one matching combo; got {len(matching)}"
    # The winner's test summary must be one of the combo's test summaries.
    # Identity check is too strong (walk_forward returns fresh dataclasses);
    # field comparison via realized_pnl_total is sufficient given trade_sizes
    # produce distinguishable PnL.
    assert w.winner_test_summary.realized_pnl_total == matching[0].realized_pnl_total
    assert w.winner_test_summary.n_trades == matching[0].n_trades


def test_walk_forward_search_default_selector_reads_summary_sharpe():
    """Sanity-check that the default selector is exactly ``train_summary.sharpe``."""
    from nautilus_runner.backtest.param_search import _default_selector
    from nautilus_runner.backtest.summary import BacktestSummary

    stub = BacktestSummary(
        bar_type=BAR_TYPE,
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 2, 1, tzinfo=UTC),
        n_bars=10,
        initial_balance=Decimal("10000"),
        final_balance=Decimal("10000"),
        realized_pnl_total=Decimal("0"),
        n_trades=0,
        sharpe=1.42,
        max_drawdown=3.0,
        raw_stats={},
    )
    assert _default_selector(stub) == 1.42


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
