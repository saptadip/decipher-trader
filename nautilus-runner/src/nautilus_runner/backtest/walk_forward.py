"""Rolling train/test walk-forward evaluator on top of ``run_backtest``.

Each iteration produces a ``WalkForwardWindow``:

- ``train_start`` / ``train_end`` — inclusive / exclusive UTC bounds of the
  train window. In this PR the strategy runs on the train window with fixed
  parameters (no optimizer yet); Session 3 will plug a parameter search here.
- ``test_start`` / ``test_end`` — inclusive / exclusive UTC bounds of the
  out-of-sample test window. ``test_start == train_end`` by construction.
- ``train_summary`` / ``test_summary`` — ``BacktestSummary`` from each run.

The caller supplies a ``strategy_factory: Callable[[], Strategy]`` that
returns a fresh strategy per invocation. Nautilus does not permit re-attaching
a stopped strategy instance, so both the train and the test run of each
window build a brand new one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_runner.backtest.runner import run_backtest
from nautilus_runner.backtest.summary import BacktestSummary


def add_months(dt: datetime, months: int) -> datetime:
    """UTC-safe month arithmetic that raises on day-of-month overflow.

    ``datetime.replace`` refuses ``day=31`` in February, so windows anchored to
    first-of-month arithmetic (the operator-facing default) stay exact.
    """
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    return dt.replace(year=year, month=month)


@dataclass
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_summary: BacktestSummary
    test_summary: BacktestSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "train_summary": self.train_summary.to_dict(),
            "test_summary": self.test_summary.to_dict(),
        }


@dataclass
class WalkForwardResult:
    windows: list[WalkForwardWindow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_windows": len(self.windows),
            "windows": [w.to_dict() for w in self.windows],
        }


def iter_windows(
    start: datetime,
    end: datetime,
    train_months: int,
    test_months: int,
    step_months: int,
) -> Iterator[tuple[datetime, datetime, datetime, datetime]]:
    """Yield ``(train_start, train_end, test_start, test_end)`` tuples.

    A window is emitted only if its ``test_end`` is on or before ``end``.
    """
    if train_months < 1 or test_months < 1 or step_months < 1:
        raise ValueError(
            f"train/test/step months must be >= 1; got "
            f"train={train_months} test={test_months} step={step_months}"
        )
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")

    train_start = start
    while True:
        train_end = add_months(train_start, train_months)
        test_end = add_months(train_end, test_months)
        if test_end > end:
            return
        yield (train_start, train_end, train_end, test_end)
        train_start = add_months(train_start, step_months)


def walk_forward(
    catalog_path: str | Path,
    bar_type: str,
    strategy_factory: Callable[[], Any],
    start: datetime,
    end: datetime,
    *,
    train_months: int,
    test_months: int,
    step_months: int,
    starting_usdt: Decimal = Decimal("10000"),
    instrument: Any | None = None,
    fee_model: Any | None = None,
) -> WalkForwardResult:
    """Run rolling train + test backtests over ``[start, end)``.

    Each iteration calls ``strategy_factory()`` twice — once for the train
    window, once for the test window — because Nautilus does not permit
    re-attaching a stopped strategy instance.
    """
    result = WalkForwardResult()
    for train_start, train_end, test_start, test_end in iter_windows(
        start, end, train_months, test_months, step_months,
    ):
        train_summary = run_backtest(
            catalog_path=catalog_path,
            bar_type=bar_type,
            strategy=strategy_factory(),
            start=train_start,
            end=train_end,
            starting_usdt=starting_usdt,
            instrument=instrument,
            fee_model=fee_model,
        )
        test_summary = run_backtest(
            catalog_path=catalog_path,
            bar_type=bar_type,
            strategy=strategy_factory(),
            start=test_start,
            end=test_end,
            starting_usdt=starting_usdt,
            instrument=instrument,
            fee_model=fee_model,
        )
        result.windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_summary=train_summary,
                test_summary=test_summary,
            )
        )
    return result
