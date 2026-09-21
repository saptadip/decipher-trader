#!/usr/bin/env python3
"""Run a single-window backtest from a Nautilus Parquet catalog.

Example:
    uv run python scripts/run_backtest.py \\
        --catalog /tmp/decipher-catalog \\
        --symbol BTCUSDT --interval 1h \\
        --start 2025-06-01 --end 2025-06-08 \\
        --fast 5 --slow 20 --out /tmp/backtest.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from nautilus_runner.backtest.instrument import build_btcusdt_perp
from nautilus_runner.backtest.runner import run_backtest

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
    p.add_argument("--symbol", required=True, help="e.g. BTCUSDT")
    p.add_argument("--interval", required=True, choices=sorted(INTERVAL_TO_BAR_SPEC.keys()))
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--strategy", choices=STRATEGY_CHOICES, default="buy_and_hold")
    p.add_argument("--trade-size", type=Decimal, default=Decimal("0.001"))
    p.add_argument("--starting-usdt", type=Decimal, default=Decimal("10000"))
    p.add_argument("--taker-fee", type=Decimal, default=Decimal("0.000180"))
    p.add_argument("--maker-fee", type=Decimal, default=Decimal("0.000200"))
    p.add_argument("--price-precision", type=int, default=2)
    p.add_argument("--size-precision", type=int, default=3)
    p.add_argument("--out", help="write JSON summary to this path; also always printed to stdout")
    return p.parse_args(argv)


def _build_buy_and_hold(args: argparse.Namespace, bar_type: BarType) -> object:
    from nautilus_runner.backtest.strategies import BuyAndHold, BuyAndHoldConfig

    cfg = BuyAndHoldConfig(
        instrument_id=InstrumentId.from_str(f"{args.symbol}-PERP.BINANCE"),
        bar_type=bar_type,
        trade_size=args.trade_size,
        size_precision=args.size_precision,
    )
    return BuyAndHold(cfg)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)

    start = datetime.combine(date.fromisoformat(args.start), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(date.fromisoformat(args.end), datetime.min.time(), tzinfo=timezone.utc)
    if end <= start:
        print(f"end ({end.date()}) must be after start ({start.date()})", file=sys.stderr)
        return 2

    spec = INTERVAL_TO_BAR_SPEC[args.interval]
    bar_type_str = f"{args.symbol}-PERP.BINANCE-{spec}-LAST-EXTERNAL"
    bar_type = BarType.from_str(bar_type_str)

    # The instrument factory hard-codes BTCUSDT-PERP.BINANCE (see
    # nautilus_runner.backtest.instrument.build_btcusdt_perp). Running with any
    # other --symbol would silently mismatch the strategy's instrument_id with
    # the engine's registered instrument; fail loudly instead.
    if args.symbol != "BTCUSDT":
        print(
            f"--symbol {args.symbol} is not supported by this CLI; only BTCUSDT is wired. "
            "See docs/backtesting.md for the pending multi-symbol follow-up.",
            file=sys.stderr,
        )
        return 2

    if args.strategy == "buy_and_hold":
        strategy = _build_buy_and_hold(args, bar_type)
    elif args.strategy == "toy_momentum":
        # ToyMomentum was written for the live-wired runtime and does not
        # currently survive BacktestEngine's on_start (see docs/backtesting.md).
        # Fail with a clear message instead of an engine-startup traceback.
        print(
            "toy_momentum is not backtest-safe in rc5 yet; use --strategy buy_and_hold. "
            "See docs/backtesting.md for the pending adaptation.",
            file=sys.stderr,
        )
        return 2
    else:  # pragma: no cover - defended by argparse `choices`
        raise ValueError(f"unknown strategy: {args.strategy}")

    instrument = build_btcusdt_perp(
        price_precision=args.price_precision,
        size_precision=args.size_precision,
        maker_fee=args.maker_fee,
        taker_fee=args.taker_fee,
    )

    try:
        summary = run_backtest(
            catalog_path=args.catalog,
            bar_type=bar_type_str,
            strategy=strategy,
            start=start,
            end=end,
            starting_usdt=args.starting_usdt,
            instrument=instrument,
        )
    except ValueError as exc:
        print(f"backtest refused: {exc}", file=sys.stderr)
        return 3

    payload = json.dumps(summary.to_dict(), indent=2, default=str)
    print(payload)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(payload)
        print(f"summary written to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
