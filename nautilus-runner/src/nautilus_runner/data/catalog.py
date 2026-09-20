"""Write bars into a Nautilus ParquetDataCatalog and funding rows into raw Parquet."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from nautilus_trader.model import Bar
from nautilus_trader.persistence import ParquetDataCatalog

FUNDING_SCHEMA = pa.schema(
    [
        pa.field("ts_ns", pa.int64()),
        pa.field("funding_rate", pa.float64()),
        pa.field("mark_price", pa.float64()),
        pa.field("symbol", pa.string()),
    ]
)


def write_bars_to_catalog(base_path: str | Path, bars: list[Bar]) -> str:
    """Persist a bar sequence to a Nautilus Parquet catalog at ``base_path``."""
    base = Path(base_path)
    base.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(base))
    return catalog.write_bars(bars)


def write_funding_parquet(path: str | Path, entries: list[dict[str, Any]]) -> Path:
    """Persist funding-rate entries to a single Parquet file at ``path``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(entries, schema=FUNDING_SCHEMA)
    pq.write_table(table, str(p))
    return p
