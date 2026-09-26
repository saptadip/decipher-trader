"""Tests for the funding-Parquet loader."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nautilus_runner.data.funding_loader import (
    default_funding_path,
    load_funding,
)


def _write(path: Path, rows: list[dict]) -> None:
    table = pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                pa.field("ts_ns", pa.int64()),
                pa.field("funding_rate", pa.float64()),
                pa.field("mark_price", pa.float64()),
                pa.field("symbol", pa.string()),
            ]
        ),
    )
    pq.write_table(table, str(path))


def test_default_funding_path_layout():
    assert default_funding_path("/tmp/cat", "BTCUSDT") == Path("/tmp/cat/funding_BTCUSDT.parquet")


def test_load_returns_empty_and_warns_on_missing_file(tmp_path: Path, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="nautilus_runner.data.funding_loader"):
        got = load_funding(tmp_path / "nope.parquet")
    assert got == []
    assert any("funding parquet not found" in r.message for r in caplog.records)


def test_load_returns_chronological_events(tmp_path: Path):
    p = tmp_path / "f.parquet"
    _write(p, [
        {"ts_ns": 3, "funding_rate": 0.001, "mark_price": 50000, "symbol": "BTCUSDT"},
        {"ts_ns": 1, "funding_rate": -0.002, "mark_price": 49000, "symbol": "BTCUSDT"},
        {"ts_ns": 2, "funding_rate": 0.0, "mark_price": 49500, "symbol": "BTCUSDT"},
    ])
    got = load_funding(p)
    assert [r["ts_ns"] for r in got] == [1, 2, 3]


def test_load_respects_start_end_window(tmp_path: Path):
    p = tmp_path / "f.parquet"
    # ts_ns bounds mid-window
    _write(p, [
        {"ts_ns": 1_000_000_000, "funding_rate": 0.001, "mark_price": 1, "symbol": "X"},
        {"ts_ns": 2_000_000_000, "funding_rate": 0.002, "mark_price": 1, "symbol": "X"},
        {"ts_ns": 3_000_000_000, "funding_rate": 0.003, "mark_price": 1, "symbol": "X"},
    ])
    utc = timezone.utc
    start = datetime.fromtimestamp(2, tz=utc)  # ts_ns = 2_000_000_000
    end = datetime.fromtimestamp(3, tz=utc)    # ts_ns = 3_000_000_000

    got = load_funding(p, start=start, end=end)
    # start inclusive, end exclusive → only ts_ns=2_000_000_000 fits
    assert [r["ts_ns"] for r in got] == [2_000_000_000]


def test_load_rejects_naive_datetime(tmp_path: Path):
    p = tmp_path / "f.parquet"
    _write(p, [{"ts_ns": 1, "funding_rate": 0.0, "mark_price": 1, "symbol": "X"}])
    with pytest.raises(ValueError, match="tz-aware"):
        load_funding(p, start=datetime(2025, 1, 1))
    with pytest.raises(ValueError, match="tz-aware"):
        load_funding(p, end=datetime(2025, 1, 1))
