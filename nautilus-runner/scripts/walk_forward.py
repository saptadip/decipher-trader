#!/usr/bin/env python3
"""Run a rolling walk-forward backtest over a Nautilus Parquet catalog.

Each iteration produces a ``WalkForwardWindow`` with a train + test
``BacktestSummary``. In this release the strategy runs with fixed parameters
on both windows; Session 3 will plug a parameter search over the train
window.

Example:
    uv run python scripts/walk_forward.py \\
        --catalog /tmp/decipher-catalog \\
        --symbol BTCUSDT --interval 1h \\
        --start 2025-01-01 --end 2025-07-01 \\
        --strategy buy_and_hold \\
        --train-months 3 --test-months 1 --step-months 1 \\
        --out /tmp/walk-forward.json
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
from nautilus_runner.backtest.walk_forward import walk_forward

INTERVAL_TO_BAR_SPEC = {
    "1m": "1-MINUTE",
    "5m": "5-MINUTE",
    "15m": "15-MINUTE",
    "1h": "1-HOUR",
    "4h": "4-HOUR",
    "1d": "1-DAY",
}

STRATEGY_CHOICES = ("buy_and_hold", "toy_momentum")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", required=True, help="Parquet catalog directory produced by download_data.py")
    p.add_argument("--symbol", required=True, help="e.g. BTCUSDT (only BTCUSDT is wired today)")
    p.add_argument("--interval", required=True, choices=sorted(INTERVAL_TO_BAR_SPEC.keys()))
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--strategy", choices=STRATEGY_CHOICES, default="buy_and_hold")
    p.add_argument("--train-months", type=int, default=3)
    p.add_argument("--test-months", type=int, default=1)
    p.add_argument("--step-months", type=int, default=1)
    p.add_argument("--trade-size", type=Decimal, default=Decimal("0.001"))
    p.add_argument("--starting-usdt", type=Decimal, default=Decimal("10000"))
    p.add_argument("--taker-fee", type=Decimal, default=Decimal("0.000180"))
    p.add_argument("--maker-fee", type=Decimal, default=Decimal("0.000200"))
    p.add_argument("--price-precision", type=int, default=2)
    p.add_argument("--size-precision", type=int, default=3)
    # ToyMomentum-only hyperparameters (ignored by buy_and_hold).
    p.add_argument("--fast", type=int, default=5, help="ToyMomentum fast MA period")
    p.add_argument("--slow", type=int, default=20, help="ToyMomentum slow MA period")
    p.add_argument("--max-position", type=float, default=0.01, help="ToyMomentum position cap")
    p.add_argument("--max-notional", type=float, default=1000.0, help="ToyMomentum notional cap")
    p.add_argument("--max-daily-loss", type=float, default=100.0, help="ToyMomentum daily-loss circuit")
    p.add_argument("--out", help="write JSON result to this path; also always printed to stdout")
    return p.parse_args(argv)


def _make_buy_and_hold_factory(args: argparse.Namespace, bar_type: BarType):
    from nautilus_runner.backtest.strategies import BuyAndHold, BuyAndHoldConfig

    def _make():
        return BuyAndHold(
            BuyAndHoldConfig(
                instrument_id=InstrumentId.from_str(f"{args.symbol}-PERP.BINANCE"),
                bar_type=bar_type,
                trade_size=args.trade_size,
                size_precision=args.size_precision,
            )
        )

    return _make


def _make_toy_momentum_factory(args: argparse.Namespace, bar_type: BarType):
    from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig

    def _make():
        return ToyMomentum(
            ToyMomentumConfig(
                instrument_id=InstrumentId.from_str(f"{args.symbol}-PERP.BINANCE"),
                bar_type=bar_type,
                trade_size=args.trade_size,
                max_notional=args.max_notional,
                max_daily_loss=args.max_daily_loss,
                max_position=args.max_position,
                fast_period=args.fast,
                slow_period=args.slow,
            )
        )

    return _make


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

    spec = INTERVAL_TO_BAR_SPEC[args.interval]
    bar_type_str = f"{args.symbol}-PERP.BINANCE-{spec}-LAST-EXTERNAL"
    bar_type = BarType.from_str(bar_type_str)

    if args.strategy == "buy_and_hold":
        factory = _make_buy_and_hold_factory(args, bar_type)
    elif args.strategy == "toy_momentum":
        factory = _make_toy_momentum_factory(args, bar_type)
    else:  # pragma: no cover - defended by argparse choices
        raise ValueError(f"unknown strategy: {args.strategy}")

    instrument = build_btcusdt_perp(
        price_precision=args.price_precision,
        size_precision=args.size_precision,
        maker_fee=args.maker_fee,
        taker_fee=args.taker_fee,
    )

    try:
        result = walk_forward(
            catalog_path=args.catalog,
            bar_type=bar_type_str,
            strategy_factory=factory,
            start=start,
            end=end,
            train_months=args.train_months,
            test_months=args.test_months,
            step_months=args.step_months,
            starting_usdt=args.starting_usdt,
            instrument=instrument,
        )
    except ValueError as exc:
        print(f"walk-forward refused: {exc}", file=sys.stderr)
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
