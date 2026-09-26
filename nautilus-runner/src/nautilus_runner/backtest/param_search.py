"""Grid parameter-search wrapper on top of ``walk_forward``.

For each parameter combination in the cartesian product of ``param_grid``:

1. Build a fresh ``strategy_factory`` that closes over that combo.
2. Run the existing ``walk_forward`` evaluator across the requested window range.
3. Collect the per-window train + test ``BacktestSummary`` under those params.

After all combos have run, assemble a per-window **winner** by evaluating a
selection criterion (default: highest train-window Sharpe from
``nautilus_runner.metrics``) on the train-window summaries. The winner's
out-of-sample test summary is then the operator's per-window scorecard.

This layer is a **compatible expansion** on top of ``walk_forward`` — it does
not touch the existing ``Callable[[], Strategy]`` factory contract, per the
"Session-3 signature note" in ``walk_forward``'s module docstring. If a future
session outgrows the grid shape (e.g. Bayesian search), the same composition
pattern still applies: build a factory per candidate, delegate to
``walk_forward``, post-process the results.

Cost note: for a grid of P combos and W windows, this runs
``P * W * 2`` engine spins (each window is train + test). Keep grids small
during exploration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any

from nautilus_runner.backtest.summary import BacktestSummary
from nautilus_runner.backtest.walk_forward import walk_forward


ParamCombo = dict[str, Any]


def iter_grid(param_grid: Mapping[str, Sequence[Any]]) -> Iterator[ParamCombo]:
    """Yield each cartesian-product combination of ``param_grid``.

    Emits combos as ``dict`` with keys sorted alphabetically for deterministic
    ordering across runs.

    Raises ``ValueError`` if any parameter has an empty sequence — an empty
    cartesian product is a silent no-op that would produce zero backtests,
    which is almost always an operator mistake.
    """
    if not param_grid:
        return
    names = sorted(param_grid.keys())
    values_lists = [list(param_grid[n]) for n in names]
    for name, values in zip(names, values_lists, strict=True):
        if not values:
            raise ValueError(f"param_grid[{name!r}] is empty")
    for combo_values in product(*values_lists):
        yield dict(zip(names, combo_values, strict=True))


@dataclass
class ParamWindowResult:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    winner_params: ParamCombo
    winner_train_summary: BacktestSummary
    winner_test_summary: BacktestSummary
    all_train_summaries: list[tuple[ParamCombo, BacktestSummary]] = field(default_factory=list)
    all_test_summaries: list[tuple[ParamCombo, BacktestSummary]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "winner_params": self.winner_params,
            "winner_train_summary": self.winner_train_summary.to_dict(),
            "winner_test_summary": self.winner_test_summary.to_dict(),
            "all_train_summaries": [
                {"params": p, "summary": s.to_dict()} for p, s in self.all_train_summaries
            ],
            "all_test_summaries": [
                {"params": p, "summary": s.to_dict()} for p, s in self.all_test_summaries
            ],
        }


@dataclass
class ParamSearchResult:
    windows: list[ParamWindowResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_windows": len(self.windows),
            "windows": [w.to_dict() for w in self.windows],
        }


def _default_selector(train_summary: BacktestSummary) -> float:
    """Selection score for a single param combo on one train window.

    Default: the runner-computed Sharpe from realized per-trade PnL
    (``nautilus_runner.metrics.sharpe_from_pnls``, populated on every
    ``BacktestSummary`` via ``run_backtest``). Higher is better. Nautilus's
    own annualized Sharpe under ``raw_stats.stats_returns`` is left in
    place for post-hoc inspection but not used for selection here — the
    per-trade Sharpe is comparable across different trade counts, which
    the annualized version is not when trade counts differ dramatically
    between windows.

    Caveats:

    - Selector return values must be finite. ``NaN`` / ``inf`` produce
      undefined winner selection because Python's ``max`` with those values
      is implementation-defined.
    - Per-trade Sharpe returns ``0.0`` for combos with fewer than two trades
      or with all-identical PnL (``stdev == 0.0``). On low-turnover
      strategies this collapses many combos into a tie at ``0.0`` and
      ``max()`` picks the first by insertion order. Override via the
      ``selector`` kwarg for exploration — e.g.
      ``lambda s: s.realized_pnl_total`` or ``lambda s: -s.max_drawdown``.
    """
    return train_summary.sharpe


def walk_forward_search(
    catalog_path: str | Path,
    bar_type: str,
    strategy_from_params: Callable[[ParamCombo], Any],
    start: datetime,
    end: datetime,
    *,
    train_months: int,
    test_months: int,
    step_months: int,
    param_grid: Mapping[str, Sequence[Any]],
    starting_usdt: Decimal = Decimal("10000"),
    instrument: Any | None = None,
    fee_model: Any | None = None,
    selector: Callable[[BacktestSummary], float] = _default_selector,
) -> ParamSearchResult:
    """Run ``walk_forward`` once per combo in ``param_grid``, pick a winner per window.

    ``strategy_from_params(params) -> Strategy`` receives a param-combo dict
    and returns a fresh strategy instance. ``walk_forward`` calls the closed-
    over factory twice per window (train + test), so this loop invokes
    ``strategy_from_params(params)`` ``2 * W`` times per combo.
    """
    combos = list(iter_grid(param_grid))
    if not combos:
        raise ValueError("param_grid produced zero combinations")

    per_combo_results = []
    for combo in combos:
        def _factory(_c=combo):  # default-arg trick pins the combo per iteration
            return strategy_from_params(_c)

        wf_result = walk_forward(
            catalog_path=catalog_path,
            bar_type=bar_type,
            strategy_factory=_factory,
            start=start,
            end=end,
            train_months=train_months,
            test_months=test_months,
            step_months=step_months,
            starting_usdt=starting_usdt,
            instrument=instrument,
            fee_model=fee_model,
        )
        per_combo_results.append((combo, wf_result))

    if not per_combo_results or not per_combo_results[0][1].windows:
        return ParamSearchResult()

    n_windows = len(per_combo_results[0][1].windows)
    # Every combo must produce the same window count — walk_forward is
    # deterministic in its window iteration and does not depend on strategy
    # behaviour, so this is a strong invariant. Raise (not ``assert``) so
    # ``python -O`` cannot silently strip the check.
    for combo, wf in per_combo_results:
        if len(wf.windows) != n_windows:
            raise RuntimeError(
                f"combo {combo!r} produced {len(wf.windows)} windows; expected {n_windows}"
            )

    result = ParamSearchResult()
    for i in range(n_windows):
        train_summaries = [(combo, wf.windows[i].train_summary) for combo, wf in per_combo_results]
        test_summaries = [(combo, wf.windows[i].test_summary) for combo, wf in per_combo_results]

        winner_combo, winner_train = max(train_summaries, key=lambda pair: selector(pair[1]))
        winner_test = next(s for c, s in test_summaries if c == winner_combo)

        first_window = per_combo_results[0][1].windows[i]
        result.windows.append(
            ParamWindowResult(
                train_start=first_window.train_start,
                train_end=first_window.train_end,
                test_start=first_window.test_start,
                test_end=first_window.test_end,
                winner_params=winner_combo,
                winner_train_summary=winner_train,
                winner_test_summary=winner_test,
                all_train_summaries=train_summaries,
                all_test_summaries=test_summaries,
            )
        )
    return result
