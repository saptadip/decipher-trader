# Historical data pipeline

Downloads Binance USDM perpetual data (OHLCV bars + funding-rate history) into a
Nautilus Parquet catalog. Session 2 (backtest infrastructure) will load this
catalog directly.

## Why Binance

The live trader executes on Hyperliquid, but Session 1 uses Binance data because:

- **Depth.** Hyperliquid's public `candleSnapshot` returns at most 5000 bars per
  call; on 1-minute bars that is roughly 3.5 days of history. Binance publishes
  monthly OHLCV archives back to 2019.
- **Simplicity.** One HTTP GET per month vs. paginated POST with rate limits.
- **Correlation.** BTC-perp price series are highly correlated across venues,
  so strategy edge that appears in Binance data will typically appear on
  Hyperliquid too, modulo fees and funding.

Session 4 (paper-forward) explicitly validates that a candidate strategy still
works against live Hyperliquid data. Small basis and different funding rates
are expected and are the exact question that Session 4 answers.

## Prerequisites

`nautilus-runner`'s `.venv` includes `pyarrow`. Sync once:

```bash
cd nautilus-runner && uv sync --extra dev
```

## Fetch a window

```bash
cd nautilus-runner
uv run python scripts/download_data.py \
  --symbol BTCUSDT --interval 1m \
  --start 2026-09-14 --end 2026-09-21 \
  --out /tmp/decipher-catalog --funding
```

Arguments:

- `--symbol` — Binance USDM symbol, e.g. `BTCUSDT`, `ETHUSDT`.
- `--interval` — bar interval; `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.
- `--start`, `--end` — UTC dates. `--end` is exclusive.
- `--out` — target catalog directory (created if missing).
- `--funding` — also fetch funding-rate history to `funding_{SYMBOL}.parquet`.
- `--skip-integrity` — write bars even if the integrity report is not clean.
- `--price-precision`, `--size-precision` — override the defaults (2, 3).
- `--max-bar-to-bar-ratio` — flag close-to-close price jumps beyond this
  multiplier (default 10x).

## Output layout

```
<out>/
├── integrity_report.json          # gap / duplicate / anomaly summary for the run
├── data/
│   └── bar/
│       └── BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL/*.parquet
└── funding_BTCUSDT.parquet        # optional, when --funding is passed
```

Bars are written via `nautilus_trader.persistence.ParquetDataCatalog.write_bars`
so they load in a `BacktestEngine` with no format translation.

Funding rows use a raw pyarrow table with schema
`[ts_ns: int64, funding_rate: float64, mark_price: float64, symbol: string]`.

## Integrity checks

Every run computes:

- **Gaps** — adjacent bars whose timestamps do not differ by exactly one
  interval.
- **Duplicates** — repeated `ts_event` values.
- **Non-positive prices** — any of open/high/low/close ≤ 0.
- **Bar-to-bar price anomalies** — close-to-close ratios above the configured
  threshold.
- **Row-count mismatch** — comparison with the expected count for the window.

The run exits non-zero (code `3`) if any check fails, unless
`--skip-integrity` is set. The full report is always written to
`integrity_report.json` next to the catalog.

## Basis caveat

Backtest results measured on Binance data will not translate one-for-one to
live Hyperliquid PnL. Expect small differences in mark price, funding, and
liquidity. Rely on Session 4's paper-forward run for venue-specific validation
before promoting a strategy to live capital.
