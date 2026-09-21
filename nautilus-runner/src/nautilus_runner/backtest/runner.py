"""Single-window backtest driver on top of Nautilus rc5 ``BacktestEngine``."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
            starting_balances=[Money.from_decimal(starting_usdt, usdt)],
            fee_model=fee_model or MakerTakerFeeModel(),
        )
        engine.add_instrument(inst)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run(start=start_ns, end=end_ns)

        result = engine.get_result()

        realized = engine.portfolio.realized_pnl(inst.id, target_currency=usdt)
        realized_total = realized.as_decimal() if realized is not None else Decimal("0")

        equity_map = engine.portfolio.equity(venue=BINANCE) or {}
        equity_money = equity_map.get(usdt)
        if equity_money is not None:
            final_balance = equity_money.as_decimal()
        else:
            logger.warning("engine.portfolio.equity(BINANCE) missing USDT entry; reporting starting balance")
            final_balance = starting_usdt

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
    """Pull the per-closed-position realized PnL column from the positions report."""
    report = engine.generate_positions_report()
    return realized_pnls_from_report(report)


def realized_pnls_from_report(report: Any) -> list[float]:
    """Parse the ``realized_pnl`` column out of a Nautilus positions report.

    The column is a ``Money`` string of the form ``"<amount> <CURRENCY>"``.
    Rows with unparseable values are logged and skipped rather than silently
    dropped — a future Nautilus format change should surface as a WARNING in
    the runner's logs rather than a phantom 0.0 Sharpe.
    """
    if report is None or getattr(report, "empty", True):
        return []
    if "realized_pnl" not in getattr(report, "columns", []):
        return []
    out: list[float] = []
    for x in report["realized_pnl"].tolist():
        if x is None:
            continue
        s = str(x).strip().split(" ", 1)[0]
        try:
            out.append(float(s))
        except ValueError:
            logger.warning("unparseable realized_pnl value %r skipped", x)
            continue
    return out
