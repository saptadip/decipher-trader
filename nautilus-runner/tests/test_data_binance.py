import io
import zipfile
from datetime import date

import httpx
import pytest
import respx

from nautilus_runner.data.binance import (
    FAPI_BASE,
    VISION_BASE,
    BinanceHistoricalClient,
    _iter_months,
    date_to_ms,
)


def _make_zip(rows: list[list[str]], inner_name: str = "BTCUSDT-1m-2026-09.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_bytes = "\n".join(",".join(r) for r in rows).encode()
        zf.writestr(inner_name, csv_bytes)
    return buf.getvalue()


def _kline_row(open_ms: int, close_ms: int, close: str = "50000.00", vol: str = "1.5") -> list[str]:
    return [
        str(open_ms),
        close,
        close,
        close,
        close,
        vol,
        str(close_ms),
        "0",
        "1",
        "0",
        "0",
        "0",
    ]


def test_iter_months_single_month_window():
    assert list(_iter_months(date(2026, 9, 14), date(2026, 9, 21))) == [(2026, 9)]


def test_iter_months_spans_year_boundary():
    got = list(_iter_months(date(2026, 12, 15), date(2027, 2, 10)))
    assert got == [(2026, 12), (2027, 1), (2027, 2)]


def test_iter_months_excludes_end_month_when_first_of_month():
    assert list(_iter_months(date(2026, 9, 1), date(2026, 10, 1))) == [(2026, 9)]


def test_iter_months_empty_on_reversed_window():
    assert list(_iter_months(date(2026, 9, 21), date(2026, 9, 14))) == []


def test_date_to_ms_utc():
    # 2026-01-01T00:00:00 UTC = 1_767_225_600_000 ms
    assert date_to_ms(date(2026, 1, 1)) == 1_767_225_600_000


@pytest.mark.asyncio
async def test_fetch_kline_rows_yields_rows_from_zip():
    rows = [
        _kline_row(1_700_000_000_000, 1_700_000_059_999, close="50001.00"),
        _kline_row(1_700_000_060_000, 1_700_000_119_999, close="50002.00"),
    ]
    zip_bytes = _make_zip(rows)

    async with respx.mock() as router:
        router.get(f"{VISION_BASE}/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip").mock(
            return_value=httpx.Response(200, content=zip_bytes),
        )
        client = BinanceHistoricalClient()
        got = []
        async for row in client.fetch_kline_rows("BTCUSDT", "1m", date(2026, 9, 14), date(2026, 9, 21)):
            got.append(row)

    assert len(got) == 2
    assert got[0][4] == "50001.00"
    assert got[1][4] == "50002.00"


@pytest.mark.asyncio
async def test_fetch_kline_rows_iterates_multiple_months():
    sep = _make_zip([_kline_row(1_700_000_000_000, 1_700_000_059_999, close="50001.00")])
    oct_ = _make_zip([_kline_row(1_701_000_000_000, 1_701_000_059_999, close="60001.00")])

    async with respx.mock() as router:
        router.get(f"{VISION_BASE}/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip").mock(
            return_value=httpx.Response(200, content=sep),
        )
        router.get(f"{VISION_BASE}/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10.zip").mock(
            return_value=httpx.Response(200, content=oct_),
        )
        client = BinanceHistoricalClient()
        got = [row async for row in client.fetch_kline_rows("BTCUSDT", "1m", date(2026, 9, 1), date(2026, 10, 5))]

    assert len(got) == 2
    assert got[0][4] == "50001.00"
    assert got[1][4] == "60001.00"


@pytest.mark.asyncio
async def test_fetch_kline_rows_skips_404_month():
    good = _make_zip([_kline_row(1_701_000_000_000, 1_701_000_059_999, close="60001.00")])

    async with respx.mock() as router:
        router.get(f"{VISION_BASE}/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip").mock(
            return_value=httpx.Response(404),
        )
        router.get(f"{VISION_BASE}/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10.zip").mock(
            return_value=httpx.Response(200, content=good),
        )
        client = BinanceHistoricalClient()
        got = [row async for row in client.fetch_kline_rows("BTCUSDT", "1m", date(2026, 9, 1), date(2026, 10, 5))]

    assert len(got) == 1
    assert got[0][4] == "60001.00"


@pytest.mark.asyncio
async def test_fetch_kline_rows_raises_on_500():
    async with respx.mock() as router:
        router.get(f"{VISION_BASE}/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip").mock(
            return_value=httpx.Response(500),
        )
        client = BinanceHistoricalClient()
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in client.fetch_kline_rows("BTCUSDT", "1m", date(2026, 9, 1), date(2026, 9, 30)):
                pass


@pytest.mark.asyncio
async def test_fetch_funding_paginates_and_stops_on_short_page():
    page_a = [
        {"symbol": "BTCUSDT", "fundingTime": 1_700_000_000_000 + i * 1_000, "fundingRate": "0.0001", "markPrice": "50000"}
        for i in range(1000)
    ]
    page_b = [
        {"symbol": "BTCUSDT", "fundingTime": 1_700_000_000_000 + 1_000_000 + i, "fundingRate": "0.0002", "markPrice": "50100"}
        for i in range(3)
    ]

    async with respx.mock(base_url=FAPI_BASE) as router:
        route = router.get("/fapi/v1/fundingRate").mock(
            side_effect=[
                httpx.Response(200, json=page_a),
                httpx.Response(200, json=page_b),
            ],
        )
        client = BinanceHistoricalClient()
        got = await client.fetch_funding("BTCUSDT", 1_700_000_000_000, 1_700_000_000_000 + 2_000_000)

    assert len(got) == 1003
    assert route.call_count == 2
    # Second call should have advanced cursor beyond the last ts from page_a.
    first_start = int(route.calls[0].request.url.params["startTime"])
    second_start = int(route.calls[1].request.url.params["startTime"])
    assert second_start > first_start


@pytest.mark.asyncio
async def test_fetch_funding_returns_empty_when_no_data():
    async with respx.mock(base_url=FAPI_BASE) as router:
        router.get("/fapi/v1/fundingRate").mock(return_value=httpx.Response(200, json=[]))
        client = BinanceHistoricalClient()
        assert await client.fetch_funding("BTCUSDT", 1, 2) == []


@pytest.mark.asyncio
async def test_fetch_funding_advances_cursor_past_last_ts():
    """After a full page, the next request's startTime must be last_ts + 1 exactly."""
    last_ts = 1_700_000_000_000 + 999
    page_a = [
        {"symbol": "BTCUSDT", "fundingTime": 1_700_000_000_000 + i, "fundingRate": "0.0001", "markPrice": "50000"}
        for i in range(1000)
    ]

    async with respx.mock(base_url=FAPI_BASE) as router:
        route = router.get("/fapi/v1/fundingRate").mock(
            side_effect=[
                httpx.Response(200, json=page_a),
                httpx.Response(200, json=[]),
            ],
        )
        client = BinanceHistoricalClient()
        got = await client.fetch_funding("BTCUSDT", 1_700_000_000_000, 1_700_000_000_000 + 10_000)

    assert len(got) == 1000
    assert route.call_count == 2
    assert int(route.calls[1].request.url.params["startTime"]) == last_ts + 1


@pytest.mark.asyncio
async def test_fetch_funding_breaks_when_full_page_reaches_end_ms():
    """A full page whose last ts >= end_ms must NOT trigger another request."""
    end_ms = 1_700_000_000_000 + 999
    page_a = [
        {"symbol": "BTCUSDT", "fundingTime": 1_700_000_000_000 + i, "fundingRate": "0.0001", "markPrice": "50000"}
        for i in range(1000)
    ]

    async with respx.mock(base_url=FAPI_BASE) as router:
        route = router.get("/fapi/v1/fundingRate").mock(
            side_effect=[httpx.Response(200, json=page_a)],
        )
        client = BinanceHistoricalClient()
        got = await client.fetch_funding("BTCUSDT", 1_700_000_000_000, end_ms)

    assert len(got) == 1000
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_fetch_funding_propagates_transport_error():
    async with respx.mock(base_url=FAPI_BASE) as router:
        router.get("/fapi/v1/fundingRate").mock(side_effect=httpx.ConnectError("boom"))
        client = BinanceHistoricalClient()
        with pytest.raises(httpx.ConnectError):
            await client.fetch_funding("BTCUSDT", 1, 2)


@pytest.mark.asyncio
async def test_fetch_kline_rows_skips_csv_header_row():
    """Some archives ship with a header row; the extractor must drop it."""
    header = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "tb_v", "tb_qv", "ignore"]
    data_row = _kline_row(1_700_000_000_000, 1_700_000_059_999, close="50001.00")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_bytes = "\n".join(",".join(r) for r in [header, data_row]).encode()
        zf.writestr("BTCUSDT-1m-2026-09.csv", csv_bytes)

    async with respx.mock() as router:
        router.get(f"{VISION_BASE}/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip").mock(
            return_value=httpx.Response(200, content=buf.getvalue()),
        )
        client = BinanceHistoricalClient()
        got = [row async for row in client.fetch_kline_rows("BTCUSDT", "1m", date(2026, 9, 1), date(2026, 10, 1))]

    assert len(got) == 1
    assert got[0][4] == "50001.00"
