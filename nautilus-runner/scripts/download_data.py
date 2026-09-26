#!/usr/bin/env python3
"""Download historical perp data (Binance USDM or Hyperliquid) into a Nautilus Parquet catalog.

Examples:
    # Binance USDM (default)
    uv run python scripts/download_data.py \\
        --source binance --symbol BTCUSDT --interval 1m \\
        --start 2026-09-14 --end 2026-09-21 \\
        --out /tmp/decipher-catalog --funding

    # Hyperliquid (coin form; venue-native)
    uv run python scripts/download_data.py \\
        --source hyperliquid --symbol BTC --interval 1h \\
        --start 2025-01-01 --end 2025-07-01 \\
        --out /tmp/hl-catalog --funding
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

from nautilus_trader.model import BarType

from nautilus_runner.data.binance import BinanceHistoricalClient
from nautilus_runner.data.binance import date_to_ms as binance_date_to_ms
from nautilus_runner.data.catalog import write_bars_to_catalog, write_funding_parquet
from nautilus_runner.data.converter import parse_funding_entry as parse_binance_funding_entry
from nautilus_runner.data.converter import parse_kline_row
from nautilus_runner.data.hyperliquid import (
    HyperliquidHistoricalClient,
    date_to_ms as hyperliquid_date_to_ms,
    parse_funding_entry as parse_hyperliquid_funding_entry,
)
from nautilus_runner.data.integrity import check_bars

SOURCE_CHOICES = ("binance", "hyperliquid")

# Per-source symbol → bar-type-prefix mapping. Binance USDM symbols keep the
# ``-PERP.BINANCE`` suffix Session 1 established; Hyperliquid uses its own
# ``-USD-PERP.HYPERLIQUID`` convention (matches nautilus_runner/main.py's live
# runner). Adding a new venue = adding a row here.
def _bar_type_prefix(source: str, symbol: str) -> str:
    if source == "binance":
        return f"{symbol}-PERP.BINANCE"
    if source == "hyperliquid":
        return f"{symbol}-USD-PERP.HYPERLIQUID"
    raise ValueError(f"unknown source: {source}")

INTERVAL_TO_NS = {
    "1m": 60 * 1_000_000_000,
    "5m": 5 * 60 * 1_000_000_000,
    "15m": 15 * 60 * 1_000_000_000,
    "1h": 60 * 60 * 1_000_000_000,
    "4h": 4 * 60 * 60 * 1_000_000_000,
    "1d": 24 * 60 * 60 * 1_000_000_000,
}

INTERVAL_TO_BAR_SPEC = {
    "1m": "1-MINUTE",
    "5m": "5-MINUTE",
    "15m": "15-MINUTE",
    "1h": "1-HOUR",
    "4h": "4-HOUR",
    "1d": "1-DAY",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=SOURCE_CHOICES, default="binance",
                   help="historical data source; symbol convention differs per source")
    p.add_argument("--symbol", required=True,
                   help="Binance: e.g. BTCUSDT; Hyperliquid: coin form e.g. BTC")
    p.add_argument("--interval", required=True, choices=sorted(INTERVAL_TO_NS.keys()))
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--out", required=True, help="output catalog directory")
    p.add_argument("--price-precision", type=int, default=2)
    p.add_argument("--size-precision", type=int, default=3)
    p.add_argument("--funding", action="store_true", help="also fetch funding-rate history")
    p.add_argument("--skip-integrity", action="store_true", help="write even if integrity checks fail")
    p.add_argument(
        "--max-bar-to-bar-ratio",
        type=float,
        default=10.0,
        help="fail bar-to-bar close jumps above this multiplier",
    )
    return p.parse_args(argv)


async def _fetch_bars_and_funding(
    args: argparse.Namespace, bar_type: BarType, start: date, end: date
) -> tuple[list, list[dict], int, int]:
    """Return ``(bars_in_window, funding_rows, start_ns, end_ns)`` for the chosen source."""
    start_ns = binance_date_to_ms(start) * 1_000_000  # UTC-midnight → ns; source-agnostic
    end_ns = binance_date_to_ms(end) * 1_000_000

    if args.source == "binance":
        client = BinanceHistoricalClient()
        print(
            f"fetching {args.symbol} {args.interval} klines from Binance {start} to {end} (exclusive)",
            flush=True,
        )
        bars_raw = []
        async for row in client.fetch_kline_rows(args.symbol, args.interval, start, end):
            bars_raw.append(parse_kline_row(row, bar_type, args.price_precision, args.size_precision))
        # Binance ships whole-month archives; trim to the requested window.
        bars = [b for b in bars_raw if start_ns <= b.ts_event < end_ns]
        print(f"received {len(bars_raw)} bars, kept {len(bars)} in window", flush=True)

        funding_rows: list[dict] = []
        if args.funding:
            # Binance treats fundingRate endTime as inclusive; align with bars' exclusive-end.
            funding_end_ms = binance_date_to_ms(end) - 1
            entries = await client.fetch_funding(args.symbol, binance_date_to_ms(start), funding_end_ms)
            funding_rows = [parse_binance_funding_entry(e) for e in entries]
        return bars, funding_rows, start_ns, end_ns

    if args.source == "hyperliquid":
        client = HyperliquidHistoricalClient()
        print(
            f"fetching {args.symbol} {args.interval} candles from Hyperliquid {start} to {end} (exclusive)",
            flush=True,
        )
        bars_raw = []
        async for row in client.fetch_candles(args.symbol, args.interval, start, end):
            bars_raw.append(parse_kline_row(row, bar_type, args.price_precision, args.size_precision))
        # Hyperliquid returns exactly the requested window (no wider-than-asked archive slabs);
        # keep the window filter for symmetry with Binance.
        bars = [b for b in bars_raw if start_ns <= b.ts_event < end_ns]
        print(f"received {len(bars_raw)} bars, kept {len(bars)} in window", flush=True)

        funding_rows = []
        if args.funding:
            funding_end_ms = hyperliquid_date_to_ms(end) - 1
            entries = await client.fetch_funding(
                args.symbol, hyperliquid_date_to_ms(start), funding_end_ms
            )
            funding_rows = [parse_hyperliquid_funding_entry(e) for e in entries]
        return bars, funding_rows, start_ns, end_ns

    raise ValueError(f"unknown source: {args.source}")  # pragma: no cover


async def _run(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end <= start:
        print(f"end ({end}) must be after start ({start})", file=sys.stderr)
        return 2

    spec = INTERVAL_TO_BAR_SPEC[args.interval]
    bar_type = BarType.from_str(f"{_bar_type_prefix(args.source, args.symbol)}-{spec}-LAST-EXTERNAL")
    interval_ns = INTERVAL_TO_NS[args.interval]

    bars, funding_rows, start_ns, end_ns = await _fetch_bars_and_funding(args, bar_type, start, end)

    expected = (end_ns - start_ns) // interval_ns
    report = check_bars(
        bars,
        interval_ns=interval_ns,
        expected_row_count=int(expected),
        max_bar_to_bar_ratio=args.max_bar_to_bar_ratio,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "integrity_report.json").write_text(json.dumps(report.to_dict(), indent=2))
    print(f"integrity ok={report.ok} gaps={len(report.gaps)} dups={len(report.duplicates)}", flush=True)

    if not report.ok and not args.skip_integrity:
        print("integrity checks failed; pass --skip-integrity to write anyway", file=sys.stderr)
        return 3

    if not bars:
        # ParquetDataCatalog.write_bars([]) is a no-op that reports success; a
        # downstream backtest would silently load an empty catalog. Refuse.
        print("no bars fell inside the requested window; nothing written", file=sys.stderr)
        return 4

    write_bars_to_catalog(out_dir, bars)
    print(f"wrote bars to catalog under {out_dir}", flush=True)

    if args.funding:
        funding_path = out_dir / f"funding_{args.symbol}.parquet"
        write_funding_parquet(funding_path, funding_rows)
        print(f"wrote {len(funding_rows)} funding entries to {funding_path}", flush=True)

    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
