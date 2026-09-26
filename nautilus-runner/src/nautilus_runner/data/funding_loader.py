"""Load a funding-rate Parquet file into a chronological list of event dicts.

Emitted by ``scripts/download_data.py --funding`` alongside the bar catalog:

    <out>/funding_{SYMBOL}.parquet   # schema: ts_ns int64, funding_rate f64,
                                     #         mark_price f64, symbol str

Loaded by ``scripts/run_backtest.py`` / ``walk_forward.py`` /
``param_search.py`` when the chosen strategy needs funding events (e.g.
``FundingReversion``). The list is passed via strategy config so the
strategy stays pure and unit-testable without any I/O.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


def load_funding(
    path: str | Path,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Read the funding Parquet and return events in ``[start, end)`` chronologically.

    ``start`` and ``end`` are UTC-aware datetimes (or ``None`` for open-ended).
    A missing file returns ``[]`` with a warning so a backtest can still run
    on a catalog that was downloaded without ``--funding``.
    """
    p = Path(path)
    if not p.exists():
        logger.warning("funding parquet not found at %s; returning empty list", p)
        return []

    table = pq.read_table(str(p))
    rows = table.to_pylist()
    rows.sort(key=lambda r: int(r["ts_ns"]))

    if start is not None:
        if start.tzinfo is None:
            raise ValueError(f"start must be tz-aware: {start!r}")
        start_ns = int(start.timestamp() * 1_000_000_000)
        rows = [r for r in rows if int(r["ts_ns"]) >= start_ns]
    if end is not None:
        if end.tzinfo is None:
            raise ValueError(f"end must be tz-aware: {end!r}")
        end_ns = int(end.timestamp() * 1_000_000_000)
        rows = [r for r in rows if int(r["ts_ns"]) < end_ns]

    return rows


def default_funding_path(catalog_path: str | Path, symbol: str) -> Path:
    """Convention: ``<catalog>/funding_{SYMBOL}.parquet``, per ``download_data.py``."""
    return Path(catalog_path) / f"funding_{symbol}.parquet"
