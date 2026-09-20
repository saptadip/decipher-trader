"""Async client for public Binance USDM historical data.

Klines are fetched from `data.binance.vision` monthly ZIP archives.
Funding-rate history is fetched from the REST endpoint at `fapi.binance.com`.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timezone
from typing import Any

import httpx

VISION_BASE = "https://data.binance.vision"
FAPI_BASE = "https://fapi.binance.com"
FUNDING_PAGE_LIMIT = 1000

logger = logging.getLogger(__name__)


def _iter_months(start: date, end_exclusive: date) -> Iterator[tuple[int, int]]:
    """Yield (year, month) pairs whose first-of-month falls in ``[start_month, end_exclusive)``."""
    if start >= end_exclusive:
        return
    y, m = start.year, start.month
    while date(y, m, 1) < end_exclusive:
        yield y, m
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def _month_kline_url(symbol: str, interval: str, year: int, month: int) -> str:
    name = f"{symbol}-{interval}-{year:04d}-{month:02d}.zip"
    return f"{VISION_BASE}/data/futures/um/monthly/klines/{symbol}/{interval}/{name}"


def _extract_zip_rows(payload: bytes) -> Iterator[list[str]]:
    """Yield CSV rows from the (single) file inside the ZIP payload."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
        if not names:
            return
        with zf.open(names[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
            reader = csv.reader(text)
            for row in reader:
                if not row:
                    continue
                # Skip header row if present (Binance sometimes ships one).
                if not row[0].isdigit():
                    continue
                yield row


class BinanceHistoricalClient:
    """Fetches historical klines + funding rates from Binance public endpoints."""

    def __init__(
        self,
        vision_base: str = VISION_BASE,
        fapi_base: str = FAPI_BASE,
        klines_timeout_secs: float = 60.0,
        funding_timeout_secs: float = 10.0,
    ) -> None:
        self._vision_base = vision_base.rstrip("/")
        self._fapi_base = fapi_base.rstrip("/")
        self._klines_timeout = klines_timeout_secs
        self._funding_timeout = funding_timeout_secs

    async def fetch_kline_rows(
        self,
        symbol: str,
        interval: str,
        start: date,
        end_exclusive: date,
    ) -> AsyncIterator[list[str]]:
        """Async-iterate raw CSV rows across the month archives that cover the window.

        A month with no published archive (404) is skipped with a warning. Any other
        HTTP or transport error propagates.
        """
        async with httpx.AsyncClient(timeout=self._klines_timeout) as c:
            for year, month in _iter_months(start, end_exclusive):
                url = _month_kline_url(symbol, interval, year, month)
                resp = await c.get(url)
                if resp.status_code == 404:
                    logger.warning("binance monthly archive missing: %s", url)
                    continue
                resp.raise_for_status()
                for row in _extract_zip_rows(resp.content):
                    yield row

    async def fetch_funding(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """Return all funding entries in ``[start_ms, end_ms]``, paginated by ``fundingTime``."""
        out: list[dict[str, Any]] = []
        cursor = start_ms
        async with httpx.AsyncClient(base_url=self._fapi_base, timeout=self._funding_timeout) as c:
            while True:
                params = {
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": FUNDING_PAGE_LIMIT,
                }
                resp = await c.get("/fapi/v1/fundingRate", params=params)
                resp.raise_for_status()
                page = resp.json()
                if not page:
                    break
                out.extend(page)
                last_ts = int(page[-1]["fundingTime"])
                if len(page) < FUNDING_PAGE_LIMIT or last_ts >= end_ms:
                    break
                cursor = last_ts + 1
        return out


def date_to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
