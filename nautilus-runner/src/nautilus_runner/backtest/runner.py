"""Single-window backtest driver on top of Nautilus rc5 ``BacktestEngine``."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LoggerConfig
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.model import AccountType, Currency, Money, OmsType
from nautilus_trader.persistence import ParquetDataCatalog

from nautilus_runner.backtest.instrument import BINANCE, build_btcusdt_perp
from nautilus_runner.backtest.summary import BacktestSummary
from nautilus_runner.metrics import max_drawdown_from_pnls, sharpe_from_pnls


def _to_ns(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError(f"datetime must be tz-aware: {dt!r}")
    return int(dt.timestamp() * 1_000_000_000)


def run_backtest(
    catalog_path: str | Path,
    bar_type: str,
    strategy: Any,
    start: datetime,
    end: datetime,
    *,
    starting_usdt: Decimal = Decimal("10000"),
    instrument: Any | None = None,
    fee_model: Any | None = None,
) -> BacktestSummary:
    """Run one strategy over one window from a Parquet catalog.

    ``start`` inclusive, ``end`` exclusive (matches ``download_data.py``).
    Refuses to run when the catalog contains zero bars in the window.
    """
    usdt = Currency.from_str("USDT")
    start_ns = _to_ns(start)
    end_ns = _to_ns(end)

    catalog = ParquetDataCatalog(str(catalog_path))
    bars = catalog.query_bars([bar_type], start=start_ns, end=end_ns)
    if not bars:
        raise ValueError(
            f"no bars for {bar_type!r} in window {start.isoformat()} to {end.isoformat()}"
        )

    inst = instrument or build_btcusdt_perp()
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=BINANCE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(float(starting_usdt), usdt)],
            fee_model=fee_model or MakerTakerFeeModel(),
        )
        engine.add_instrument(inst)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run(start=start_ns, end=end_ns)

        result = engine.get_result()

        realized = engine.portfolio.realized_pnl(inst.id, target_currency=usdt)
        realized_total = Decimal(str(float(realized))) if realized is not None else Decimal("0")

        equity_map = engine.portfolio.equity(venue=BINANCE) or {}
        equity_money = equity_map.get(usdt)
        final_balance = Decimal(str(float(equity_money))) if equity_money is not None else starting_usdt

        pnl_series = _extract_realized_pnl_series(engine)
        sharpe = sharpe_from_pnls(pnl_series)
        max_dd = max_drawdown_from_pnls(pnl_series)

        raw_stats = {
            "stats_general": dict(result.stats_general),
            "stats_pnls": {k: dict(v) for k, v in result.stats_pnls.items()},
            "stats_returns": dict(result.stats_returns),
            "total_orders": result.total_orders,
            "total_positions": result.total_positions,
            "total_events": result.total_events,
            "elapsed_time_secs": result.elapsed_time_secs,
        }

        return BacktestSummary(
            bar_type=bar_type,
            start=start.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
            n_bars=len(bars),
            initial_balance=starting_usdt,
            final_balance=final_balance,
            realized_pnl_total=realized_total,
            n_trades=result.total_positions,
            sharpe=sharpe,
            max_drawdown=max_dd,
            raw_stats=raw_stats,
        )
    finally:
        engine.dispose()


def _extract_realized_pnl_series(engine: BacktestEngine) -> list[float]:
    """Pull the per-closed-position realized PnL column from the positions report.

    Nautilus formats ``Money`` values as ``"<amount> <CURRENCY>"``; strip the
    currency suffix before converting.
    """
    report = engine.generate_positions_report()
    if report is None or getattr(report, "empty", True):
        return []
    if "realized_pnl" not in report.columns:
        return []
    out: list[float] = []
    for x in report["realized_pnl"].tolist():
        if x is None:
            continue
        s = str(x).strip().split()[0]
        try:
            out.append(float(s))
        except ValueError:
            continue
    return out
