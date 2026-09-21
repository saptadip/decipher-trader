# Backtesting

Session 2 wires Nautilus's own `BacktestEngine` on top of the Parquet catalog
that `scripts/download_data.py` writes in Session 1. One command consumes a
catalog and produces a JSON summary with realized PnL, Sharpe, max drawdown,
and raw Nautilus stats.

## Prerequisites

`nautilus-runner`'s `.venv` includes `pyarrow` and `pandas`. Sync once:

```bash
cd nautilus-runner && uv sync --extra dev
```

## Run a backtest

```bash
cd nautilus-runner
uv run python scripts/run_backtest.py \
  --catalog /tmp/decipher-catalog \
  --symbol BTCUSDT --interval 1h \
  --start 2025-06-01 --end 2025-06-08 \
  --strategy buy_and_hold \
  --starting-usdt 10000 \
  --out /tmp/backtest.json
```

Arguments:

- `--catalog` — Parquet catalog directory produced by `download_data.py`.
- `--symbol` / `--interval` — must match the bar-type prefix already written into the catalog.
- `--start` / `--end` — UTC dates. `--end` is exclusive.
- `--strategy` — `buy_and_hold` (default demo strategy that proves the plumbing) or `toy_momentum` (see caveat below).
- `--starting-usdt` — venue account starting balance (USDT).
- `--taker-fee` / `--maker-fee` — override the Binance USDM tier-0 defaults (18 bps taker, 20 bps maker).
- `--fast` / `--slow` / `--trade-size` / `--max-position` / `--max-notional` / `--max-daily-loss` — `toy_momentum` hyperparameters (ignored by `buy_and_hold`).
- `--out` — write the JSON summary to a file; the summary is also printed to stdout.

Exit codes:

- `0` — backtest ran and a summary was written.
- `2` — `--end` on or before `--start`.
- `3` — catalog contains zero bars in the requested window; nothing to run.

## Summary shape

```json
{
  "bar_type": "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL",
  "start": "2025-06-01T00:00:00+00:00",
  "end":   "2025-06-08T00:00:00+00:00",
  "n_bars": 168,
  "initial_balance": "10000",
  "final_balance": "10001.06101508",
  "realized_pnl_total": "1.06101508",
  "n_trades": 1,
  "sharpe": 0.0,
  "max_drawdown": 0.0,
  "raw_stats": {
    "stats_pnls": { "USDT": { "PnL (total)": 1.06, "Win Rate": 1.0, ... } },
    "stats_returns": { "Sharpe Ratio (252 days)": 1.28, ... },
    "total_orders": 2,
    "total_positions": 1,
    ...
  }
}
```

The `sharpe` and `max_drawdown` fields at the top level are computed by
`nautilus_runner.metrics` from the per-trade realized-PnL series (see the rc5
caveat in `metrics.py`). Nautilus's own annualized Sharpe and Sortino live under
`raw_stats.stats_returns` and are also emitted.

## Strategy caveat: `toy_momentum`

`ToyMomentum` was written for live trading and assumes a live-wired runtime
(socket-state events, audit writer, position reconciliation via the venue).
Running it inside `BacktestEngine` in rc5 currently fails during strategy
`on_start`. A follow-up PR will adapt `ToyMomentum` to be backtest-safe; until
then, use `--strategy buy_and_hold` for smoke tests and Session-3 strategy
exploration will introduce a purpose-built strategy of its own.

## Baseline metrics (targets for Session 3)

Session 3 will only consider a strategy candidate promotable when it produces
on the backtest catalog:

- **Sharpe > 1** (annualized, from `raw_stats.stats_returns`).
- **Max drawdown < 20%** of starting balance.
- **≥ 500 trades** across the walk-forward window.
- **Positive edge after 5 bps taker fee** (`realized_pnl_total > 0` with `--taker-fee 0.0005`).

The `buy_and_hold` demo does not meet these targets — it is only for
plumbing verification.
