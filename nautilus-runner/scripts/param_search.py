#!/usr/bin/env python3
"""Grid parameter search across walk-forward windows.

Each --*-grid flag takes a comma-separated list; the cartesian product of the
supplied grids becomes the candidate combos. Per window, the combo with the
highest train Sharpe is picked as the winner, and its OOS test summary is
reported.

Example (ToyMomentum fast/slow grid, 6 combos over a 6-month window):
    uv run python scripts/param_search.py \\
        --catalog /tmp/decipher-catalog \\
        --symbol BTCUSDT --interval 1h \\
        --start 2025-01-01 --end 2025-07-01 \\
        --strategy toy_momentum \\
        --fast-grid 3,5,10 --slow-grid 20,50 \\
        --train-months 3 --test-months 1 --step-months 1 \\
        --out /tmp/param-search.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import BarType, InstrumentId

from nautilus_runner.backtest.instrument import build_btcusdt_perp
from nautilus_runner.backtest.param_search import walk_forward_search

INTERVAL_TO_BAR_SPEC = {
    "1m": "1-MINUTE",
    "5m": "5-MINUTE",
    "15m": "15-MINUTE",
    "1h": "1-HOUR",
    "4h": "4-HOUR",
    "1d": "1-DAY",
}

STRATEGY_CHOICES = ("buy_and_hold", "toy_momentum", "funding_reversion")


def _parse_int_grid(value: str) -> list[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def _parse_float_grid(value: str) -> list[float]:
    return [float(x.strip()) for x in value.split(",") if x.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", required=True, help="Parquet catalog directory")
    p.add_argument("--symbol", required=True, help="e.g. BTCUSDT (only BTCUSDT is wired today)")
    p.add_argument("--interval", required=True, choices=sorted(INTERVAL_TO_BAR_SPEC.keys()))
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--strategy", choices=STRATEGY_CHOICES, default="toy_momentum")
    p.add_argument("--train-months", type=int, default=3)
    p.add_argument("--test-months", type=int, default=1)
    p.add_argument("--step-months", type=int, default=1)
    p.add_argument("--starting-usdt", type=Decimal, default=Decimal("10000"))
    p.add_argument("--taker-fee", type=Decimal, default=Decimal("0.000180"))
    p.add_argument("--maker-fee", type=Decimal, default=Decimal("0.000200"))
    p.add_argument("--price-precision", type=int, default=2)
    p.add_argument("--size-precision", type=int, default=3)
    # ToyMomentum grid (ignored by buy_and_hold; buy_and_hold uses --trade-size-grid).
    p.add_argument("--fast-grid", type=_parse_int_grid, default=[5], help="comma-separated fast MA periods")
    p.add_argument("--slow-grid", type=_parse_int_grid, default=[20], help="comma-separated slow MA periods")
    p.add_argument("--max-position", type=float, default=0.01, help="ToyMomentum position cap (fixed)")
    p.add_argument("--max-notional", type=float, default=1000.0, help="ToyMomentum notional cap (fixed)")
    p.add_argument("--max-daily-loss", type=float, default=100.0, help="ToyMomentum daily-loss (fixed)")
    p.add_argument(
        "--trade-size-grid",
        type=lambda v: [Decimal(x.strip()) for x in v.split(",") if x.strip()],
        default=[Decimal("0.001")],
        help="comma-separated trade sizes (only used by buy_and_hold)",
    )
    # FundingReversion-only grids.
    p.add_argument("--entry-threshold-grid", type=_parse_float_grid, default=[0.001],
                   help="comma-separated FundingReversion |rate| entry thresholds")
    p.add_argument("--exit-threshold-grid", type=_parse_float_grid, default=[0.0001],
                   help="comma-separated FundingReversion |rate| exit thresholds")
    p.add_argument("--out", help="write JSON result to this path; also always printed to stdout")
    return p.parse_args(argv)


def _build_grid(args: argparse.Namespace) -> dict[str, list]:
    if args.strategy == "toy_momentum":
        return {"fast": args.fast_grid, "slow": args.slow_grid}
    if args.strategy == "buy_and_hold":
        return {"trade_size": [str(x) for x in args.trade_size_grid]}
    if args.strategy == "funding_reversion":
        return {
            "entry_threshold": args.entry_threshold_grid,
            "exit_threshold": args.exit_threshold_grid,
        }
    raise ValueError(f"unknown strategy: {args.strategy}")  # pragma: no cover


def _make_strategy_from_params(args: argparse.Namespace, bar_type: BarType):
    instrument_id = InstrumentId.from_str(f"{args.symbol}-PERP.BINANCE")
    if args.strategy == "buy_and_hold":
        from nautilus_runner.backtest.strategies import BuyAndHold, BuyAndHoldConfig

        def _make(params: dict):
            return BuyAndHold(
                BuyAndHoldConfig(
                    instrument_id=instrument_id,
                    bar_type=bar_type,
                    trade_size=Decimal(str(params["trade_size"])),
                    size_precision=args.size_precision,
                )
            )

        return _make

    if args.strategy == "toy_momentum":
        from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig

        def _make(params: dict):
            return ToyMomentum(
                ToyMomentumConfig(
                    instrument_id=instrument_id,
                    bar_type=bar_type,
                    trade_size=args.trade_size_grid[0],  # fixed for toy_momentum grid
                    max_notional=args.max_notional,
                    max_daily_loss=args.max_daily_loss,
                    max_position=args.max_position,
                    fast_period=params["fast"],
                    slow_period=params["slow"],
                )
            )

        return _make

    if args.strategy == "funding_reversion":
        from nautilus_runner.data.funding_loader import default_funding_path, load_funding
        from strategies.funding_reversion.strategy import (
            FundingReversion,
            FundingReversionConfig,
        )

        start = datetime.combine(date.fromisoformat(args.start), datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(date.fromisoformat(args.end), datetime.min.time(), tzinfo=timezone.utc)
        events = load_funding(default_funding_path(args.catalog, args.symbol), start=start, end=end)

        def _make(params: dict):
            return FundingReversion(
                FundingReversionConfig(
                    instrument_id=instrument_id,
                    bar_type=bar_type,
                    funding_events=events,
                    entry_threshold=params["entry_threshold"],
                    exit_threshold=params["exit_threshold"],
                    # trade_size fixed: --trade-size-grid is owned by buy_and_hold in
                    # _GRID_OWNERS, so funding_reversion cannot vary trade_size in a
                    # grid today. If a future user wants a trade_size axis here, add
                    # a dedicated --funding-trade-size-grid flag and register it as
                    # funding_reversion-owned in _GRID_OWNERS.
                    trade_size=args.trade_size_grid[0],
                    max_notional=args.max_notional,
                    max_daily_loss=args.max_daily_loss,
                    max_position=args.max_position,
                )
            )

        return _make

    raise ValueError(f"unknown strategy: {args.strategy}")  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)

    start = datetime.combine(date.fromisoformat(args.start), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(date.fromisoformat(args.end), datetime.min.time(), tzinfo=timezone.utc)
    if end <= start:
        print(f"end ({end.date()}) must be after start ({start.date()})", file=sys.stderr)
        return 2

    if args.symbol != "BTCUSDT":
        print(
            f"--symbol {args.symbol} is not supported; only BTCUSDT is wired. "
            "See docs/backtesting.md for the pending multi-symbol follow-up.",
            file=sys.stderr,
        )
        return 2

    for name, value in (
        ("--train-months", args.train_months),
        ("--test-months", args.test_months),
        ("--step-months", args.step_months),
    ):
        if value < 1:
            print(f"{name} must be >= 1; got {value}", file=sys.stderr)
            return 2

    # Silently-ignoring a grid flag that belongs to a different strategy
    # burns operator time on a long sweep. Detect a non-default value on the
    # wrong strategy and refuse before any engine spin.
    #
    # Each grid flag → the strategy that owns it. Non-owning strategies must
    # not receive a non-default value.
    _GRID_OWNERS = {
        "--fast-grid": ("toy_momentum", args.fast_grid, [5]),
        "--slow-grid": ("toy_momentum", args.slow_grid, [20]),
        "--trade-size-grid": ("buy_and_hold", args.trade_size_grid, [Decimal("0.001")]),
        "--entry-threshold-grid": ("funding_reversion", args.entry_threshold_grid, [0.001]),
        "--exit-threshold-grid": ("funding_reversion", args.exit_threshold_grid, [0.0001]),
    }
    wrong = [
        (flag, owner)
        for flag, (owner, got, default) in _GRID_OWNERS.items()
        if owner != args.strategy and got != default
    ]
    if wrong:
        detail = ", ".join(f"{flag} (owned by --strategy {owner})" for flag, owner in wrong)
        print(
            f"grid flag(s) supplied for the wrong strategy — would be silently ignored "
            f"under --strategy {args.strategy}: {detail}",
            file=sys.stderr,
        )
        return 2

    # For toy_momentum: reject any grid where slow <= fast so we fail fast
    # (ToyMomentumConfig itself asserts on construction; catching earlier gives
    # a friendlier CLI error than a stack trace on the first combo).
    if args.strategy == "toy_momentum":
        bad = [(f, s) for f in args.fast_grid for s in args.slow_grid if s <= f]
        if bad:
            print(
                f"invalid ToyMomentum grid: slow must exceed fast; offending pairs: {bad}",
                file=sys.stderr,
            )
            return 2

    # For funding_reversion: reject non-positive entry, negative exit, and
    # exit >= entry pairs (FundingReversionConfig itself raises on construction).
    if args.strategy == "funding_reversion":
        if any(e <= 0 for e in args.entry_threshold_grid):
            print(
                "invalid FundingReversion grid: --entry-threshold-grid must all be > 0",
                file=sys.stderr,
            )
            return 2
        if any(e < 0 for e in args.exit_threshold_grid):
            print(
                "invalid FundingReversion grid: --exit-threshold-grid must all be >= 0",
                file=sys.stderr,
            )
            return 2
        bad = [
            (en, ex) for en in args.entry_threshold_grid for ex in args.exit_threshold_grid
            if ex >= en
        ]
        if bad:
            print(
                f"invalid FundingReversion grid: exit_threshold must be strictly less "
                f"than entry_threshold; offending pairs: {bad}",
                file=sys.stderr,
            )
            return 2

    spec = INTERVAL_TO_BAR_SPEC[args.interval]
    bar_type_str = f"{args.symbol}-PERP.BINANCE-{spec}-LAST-EXTERNAL"
    bar_type = BarType.from_str(bar_type_str)

    grid = _build_grid(args)
    make_strategy = _make_strategy_from_params(args, bar_type)

    instrument = build_btcusdt_perp(
        price_precision=args.price_precision,
        size_precision=args.size_precision,
        maker_fee=args.maker_fee,
        taker_fee=args.taker_fee,
    )

    try:
        result = walk_forward_search(
            catalog_path=args.catalog,
            bar_type=bar_type_str,
            strategy_from_params=make_strategy,
            start=start,
            end=end,
            train_months=args.train_months,
            test_months=args.test_months,
            step_months=args.step_months,
            param_grid=grid,
            starting_usdt=args.starting_usdt,
            instrument=instrument,
        )
    except ValueError as exc:
        print(f"param-search refused: {exc}", file=sys.stderr)
        return 3

    if not result.windows:
        print(
            "no walk-forward windows fit in the range "
            f"[{args.start}, {args.end}) with train={args.train_months}m "
            f"test={args.test_months}m step={args.step_months}m",
            file=sys.stderr,
        )
        return 4

    payload = json.dumps(result.to_dict(), indent=2, default=str)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload)
        print(f"result written to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
