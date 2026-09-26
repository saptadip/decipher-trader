"""Async client for public Hyperliquid historical data.

Hyperliquid exposes a single ``POST /info`` endpoint with a ``type`` field
that discriminates the query. Two queries matter to us:

- ``type=candleSnapshot`` — OHLCV bars for a coin/interval, capped at
  **5000 candles per call**. At 1h that is ~208 days; longer ranges need
  chunked calls.
- ``type=fundingHistory`` — funding-rate history for a coin, capped at
  **500 entries per call**. Hyperliquid funding fires hourly (unlike
  Binance USDM's 8-hourly cadence), so 500 entries covers ~20 days;
  longer ranges need chunked calls.

Both queries are public (no key, no signature).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from typing import Any

import httpx

INFO_BASE = "https://api.hyperliquid.xyz"
CANDLE_LIMIT = 5000
FUNDING_LIMIT = 500

logger = logging.getLogger(__name__)


# Hyperliquid ``interval`` strings mirror the ones a user types on the CLI, so
# the download tool can pass them through directly.
_HYPERLIQUID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M"}


def _interval_ms(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1]) * 60 * 1000
    if interval.endswith("h"):
        return int(interval[:-1]) * 60 * 60 * 1000
    if interval.endswith("d"):
        return int(interval[:-1]) * 24 * 60 * 60 * 1000
    if interval.endswith("w"):
        return int(interval[:-1]) * 7 * 24 * 60 * 60 * 1000
    if interval.endswith("M"):
        # Approximate — Hyperliquid uses calendar months but we only need this
        # for chunk-size planning, not exact alignment.
        return int(interval[:-1]) * 30 * 24 * 60 * 60 * 1000
    raise ValueError(f"unsupported Hyperliquid interval: {interval!r}")


def date_to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class HyperliquidHistoricalClient:
    """Fetches historical candles + funding rates from ``POST /info``."""

    def __init__(
        self,
        info_base: str = INFO_BASE,
        candles_timeout_secs: float = 30.0,
        funding_timeout_secs: float = 10.0,
    ) -> None:
        self._info_base = info_base.rstrip("/")
        self._candles_timeout = candles_timeout_secs
        self._funding_timeout = funding_timeout_secs

    async def fetch_candles(
        self,
        coin: str,
        interval: str,
        start: date,
        end_exclusive: date,
    ) -> AsyncIterator[list[Any]]:
        """Async-iterate raw candle dicts across the window in 5000-candle chunks.

        Each yield is a list matching Binance's kline row shape enough for
        ``nautilus_runner.data.converter.parse_kline_row`` to consume:
        ``[open_time_ms, open, high, low, close, volume, close_time_ms, ...]``.
        """
        if interval not in _HYPERLIQUID_INTERVALS:
            raise ValueError(
                f"unsupported Hyperliquid interval {interval!r}; must be one of "
                f"{sorted(_HYPERLIQUID_INTERVALS)}"
            )
        interval_ms = _interval_ms(interval)
        chunk_ms = CANDLE_LIMIT * interval_ms

        start_ms = date_to_ms(start)
        end_ms = date_to_ms(end_exclusive)
        if end_ms <= start_ms:
            return

        cursor = start_ms
        async with httpx.AsyncClient(base_url=self._info_base, timeout=self._candles_timeout) as c:
            while cursor < end_ms:
                chunk_end = min(cursor + chunk_ms, end_ms)
                body = {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": coin,
                        "interval": interval,
                        "startTime": cursor,
                        "endTime": chunk_end,
                    },
                }
                resp = await c.post("/info", json=body)
                resp.raise_for_status()
                page = resp.json()
                if not page:
                    break
                for candle in page:
                    yield _candle_to_kline_row(candle)
                # Advance to just past the last bar's open time.
                last_open = int(page[-1]["t"])
                if last_open + interval_ms >= end_ms:
                    return
                cursor = last_open + interval_ms

    async def fetch_funding(
        self,
        coin: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """Return all funding entries in ``[start_ms, end_ms]``, paginated in 500-entry chunks."""
        out: list[dict[str, Any]] = []
        cursor = start_ms
        async with httpx.AsyncClient(base_url=self._info_base, timeout=self._funding_timeout) as c:
            while True:
                body = {
                    "type": "fundingHistory",
                    "coin": coin,
                    "startTime": cursor,
                    "endTime": end_ms,
                }
                resp = await c.post("/info", json=body)
                resp.raise_for_status()
                page = resp.json()
                if not page:
                    break
                out.extend(page)
                last_ts = int(page[-1]["time"])
                if len(page) < FUNDING_LIMIT or last_ts >= end_ms:
                    break
                cursor = last_ts + 1
        return out


def _candle_to_kline_row(candle: dict[str, Any]) -> list[str]:
    """Adapt Hyperliquid's candle-dict shape to the Binance kline row shape.

    Hyperliquid's ``candleSnapshot`` response items have keys ``t`` (open ms),
    ``T`` (close ms), ``o`` / ``h`` / ``l`` / ``c`` (prices) and ``v`` (volume).
    ``parse_kline_row`` expects a list ordered as
    ``[open_time_ms, open, high, low, close, volume, close_time_ms, ...]``.
    We fill the trailing positional slots with zeros — the parser only reads
    columns 0-6.
    """
    return [
        str(int(candle["t"])),
        str(candle["o"]),
        str(candle["h"]),
        str(candle["l"]),
        str(candle["c"]),
        str(candle["v"]),
        str(int(candle["T"])),
        "0",
        "0",
        "0",
        "0",
        "0",
    ]


def parse_funding_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a Hyperliquid ``fundingHistory`` entry to the local Parquet schema.

    Input keys per Hyperliquid docs: ``coin``, ``fundingRate`` (string decimal),
    ``premium`` (string decimal), ``time`` (ms).
    """
    from nautilus_runner.data.converter import MS_TO_NS, _parse_optional_float

    return {
        "ts_ns": int(entry["time"]) * MS_TO_NS,
        "funding_rate": _parse_optional_float(entry.get("fundingRate")),
        # Hyperliquid does not report a per-event mark price on this endpoint;
        # keep the schema slot for cross-source uniformity but leave it NaN.
        "mark_price": float("nan"),
        "symbol": str(entry["coin"]),
    }
