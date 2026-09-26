"""Tests for the Hyperliquid public POST /info fetcher."""

from __future__ import annotations

import math
from datetime import date

import httpx
import pytest
import respx

from nautilus_runner.data.hyperliquid import (
    INFO_BASE,
    HyperliquidHistoricalClient,
    _candle_to_kline_row,
    date_to_ms,
    parse_funding_entry,
)


def _candle(t_ms: int, T_ms: int, close: str = "50000.0") -> dict:
    return {"t": t_ms, "T": T_ms, "o": close, "h": close, "l": close, "c": close, "v": "1.5"}


# ---------------------------------------------------------------------------
# candle → kline row adapter
# ---------------------------------------------------------------------------


def test_candle_to_kline_row_preserves_all_seven_columns():
    row = _candle_to_kline_row(_candle(1_700_000_000_000, 1_700_000_003_599_999, close="50001.5"))
    assert row[0] == "1700000000000"
    assert row[1] == "50001.5"
    assert row[2] == "50001.5"
    assert row[3] == "50001.5"
    assert row[4] == "50001.5"
    assert row[5] == "1.5"
    assert row[6] == "1700000003599999"


def test_date_to_ms_matches_utc_midnight():
    assert date_to_ms(date(2025, 1, 1)) == 1_735_689_600_000


# ---------------------------------------------------------------------------
# fetch_candles pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_candles_yields_all_rows_in_a_single_chunk():
    candles = [
        _candle(1_700_000_000_000, 1_700_000_003_599_999, close="50001.0"),
        _candle(1_700_000_003_600_000, 1_700_000_007_199_999, close="50002.0"),
    ]
    async with respx.mock(base_url=INFO_BASE) as router:
        router.post("/info").mock(return_value=httpx.Response(200, json=candles))
        client = HyperliquidHistoricalClient()
        rows = [row async for row in client.fetch_candles("BTC", "1h", date(2025, 1, 1), date(2025, 1, 2))]
    assert len(rows) == 2
    assert rows[0][4] == "50001.0"
    assert rows[1][4] == "50002.0"


@pytest.mark.asyncio
async def test_fetch_candles_paginates_when_window_exceeds_chunk_cap():
    """A window wider than ``CANDLE_LIMIT * interval_ms`` must issue multiple POSTs."""
    chunk_a = [_candle(1_700_000_000_000 + i * 3_600_000, 1_700_000_003_599_999 + i * 3_600_000, close=f"5000{i}.0") for i in range(5000)]
    chunk_b = [_candle(1_700_000_000_000 + (5000 + i) * 3_600_000, 1_700_000_003_599_999 + (5000 + i) * 3_600_000, close="60000.0") for i in range(10)]

    async with respx.mock(base_url=INFO_BASE) as router:
        # Empty third response terminates the loop cleanly.
        route = router.post("/info").mock(
            side_effect=[
                httpx.Response(200, json=chunk_a),
                httpx.Response(200, json=chunk_b),
                httpx.Response(200, json=[]),
            ],
        )
        client = HyperliquidHistoricalClient()
        # 5010 bars at 1h ≈ 208.75 days; force a range that fits.
        rows = [row async for row in client.fetch_candles("BTC", "1h", date(2025, 1, 1), date(2025, 9, 1))]
    assert route.call_count >= 2
    assert len(rows) == 5010


@pytest.mark.asyncio
async def test_fetch_candles_rejects_unknown_interval():
    client = HyperliquidHistoricalClient()
    with pytest.raises(ValueError, match="unsupported"):
        async for _ in client.fetch_candles("BTC", "17s", date(2025, 1, 1), date(2025, 1, 2)):
            pass


@pytest.mark.asyncio
async def test_fetch_candles_empty_window_yields_nothing():
    client = HyperliquidHistoricalClient()
    rows = [row async for row in client.fetch_candles("BTC", "1h", date(2025, 1, 5), date(2025, 1, 1))]
    assert rows == []


# ---------------------------------------------------------------------------
# fetch_funding pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_funding_advances_cursor_past_last_ts():
    page_a = [{"coin": "BTC", "time": 1_700_000_000_000 + i, "fundingRate": "0.0001", "premium": "0.0"} for i in range(500)]
    async with respx.mock(base_url=INFO_BASE) as router:
        route = router.post("/info").mock(
            side_effect=[
                httpx.Response(200, json=page_a),
                httpx.Response(200, json=[]),
            ],
        )
        client = HyperliquidHistoricalClient()
        got = await client.fetch_funding("BTC", 1_700_000_000_000, 1_700_000_000_000 + 10_000)
    assert len(got) == 500
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_fetch_funding_stops_on_short_page():
    page = [{"coin": "BTC", "time": 1_700_000_000_000 + i, "fundingRate": "0.0001", "premium": "0.0"} for i in range(3)]
    async with respx.mock(base_url=INFO_BASE) as router:
        route = router.post("/info").mock(return_value=httpx.Response(200, json=page))
        client = HyperliquidHistoricalClient()
        got = await client.fetch_funding("BTC", 1_700_000_000_000, 1_700_000_100_000)
    assert len(got) == 3
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_fetch_funding_returns_empty_on_no_data():
    async with respx.mock(base_url=INFO_BASE) as router:
        router.post("/info").mock(return_value=httpx.Response(200, json=[]))
        client = HyperliquidHistoricalClient()
        assert await client.fetch_funding("BTC", 1, 2) == []


# ---------------------------------------------------------------------------
# parse_funding_entry
# ---------------------------------------------------------------------------


def test_parse_funding_entry_maps_fields_and_marks_price_nan():
    entry = {"coin": "BTC", "time": 1_700_000_000_000, "fundingRate": "0.00012345", "premium": "0.0002"}
    out = parse_funding_entry(entry)
    assert out["ts_ns"] == 1_700_000_000_000 * 1_000_000
    assert out["funding_rate"] == 0.00012345
    assert math.isnan(out["mark_price"])
    assert out["symbol"] == "BTC"


def test_parse_funding_entry_tolerates_empty_funding_rate():
    entry = {"coin": "BTC", "time": 1_700_000_000_000, "fundingRate": "", "premium": ""}
    out = parse_funding_entry(entry)
    assert math.isnan(out["funding_rate"])
