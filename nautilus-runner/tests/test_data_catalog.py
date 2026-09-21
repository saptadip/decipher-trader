from pathlib import Path

import pyarrow.parquet as pq
from nautilus_trader.model import Bar, BarType, Price, Quantity

from nautilus_runner.data.catalog import write_bars_to_catalog, write_funding_parquet

BAR_TYPE = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
MINUTE_NS = 60 * 1_000_000_000


def _bar(ts_ns: int, close: float = 50000.0) -> Bar:
    p = Price(close, 2)
    return Bar(BAR_TYPE, p, p, p, p, Quantity(1.0, 3), ts_ns, ts_ns + MINUTE_NS - 1)


def test_write_bars_to_catalog_persists_parquet(tmp_path: Path):
    bars = [_bar(i * MINUTE_NS, close=50000.0 + i) for i in range(1, 6)]
    result = write_bars_to_catalog(tmp_path, bars)

    assert isinstance(result, str)
    written = list(tmp_path.rglob("*.parquet"))
    assert len(written) >= 1
    # Row count across all written files matches input.
    total = sum(pq.read_table(str(f)).num_rows for f in written)
    assert total == 5


def test_write_funding_parquet_roundtrip(tmp_path: Path):
    entries = [
        {"ts_ns": 1_700_000_000_000_000_000, "funding_rate": 0.0001, "mark_price": 50000.0, "symbol": "BTCUSDT"},
        {"ts_ns": 1_700_000_060_000_000_000, "funding_rate": 0.0002, "mark_price": 50100.5, "symbol": "BTCUSDT"},
    ]
    path = tmp_path / "funding.parquet"
    written = write_funding_parquet(path, entries)

    assert written == path
    table = pq.read_table(str(path))
    assert table.num_rows == 2
    assert table.column("symbol").to_pylist() == ["BTCUSDT", "BTCUSDT"]
    assert table.column("funding_rate").to_pylist() == [0.0001, 0.0002]
