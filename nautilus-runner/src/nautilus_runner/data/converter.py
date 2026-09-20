"""Convert raw Binance kline / funding rows into Nautilus domain types."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any

from nautilus_trader.model import Bar, BarType, Price, Quantity

MS_TO_NS = 1_000_000


def _price(value: str, precision: int) -> Price:
    return Price.from_decimal_dp(Decimal(value), precision)


def _quantity(value: str, precision: int) -> Quantity:
    return Quantity.from_decimal_dp(Decimal(value), precision)


def parse_kline_row(
    row: list[str],
    bar_type: BarType,
    price_precision: int,
    size_precision: int,
) -> Bar:
    """Convert a Binance USDM monthly kline CSV row to a Nautilus Bar.

    Row layout (12 cols): open_time_ms, open, high, low, close, volume,
    close_time_ms, quote_volume, count, taker_buy_vol, taker_buy_quote_vol, ignore.
    ``ts_event`` is set to open_time (ns); ``ts_init`` to close_time (ns).

    Values are parsed via ``Decimal`` to preserve exact tick precision — see
    AGENTS.md ("preserve exact arithmetic for prices, quantities, money, fees").
    """
    if len(row) < 7:
        raise ValueError(f"Kline row must have >= 7 columns, got {len(row)}: {row!r}")

    open_time_ns = int(row[0]) * MS_TO_NS
    close_time_ns = int(row[6]) * MS_TO_NS

    return Bar(
        bar_type,
        _price(row[1], price_precision),
        _price(row[2], price_precision),
        _price(row[3], price_precision),
        _price(row[4], price_precision),
        _quantity(row[5], size_precision),
        open_time_ns,
        close_time_ns,
    )


def parse_kline_rows(
    rows: Iterable[list[str]],
    bar_type: BarType,
    price_precision: int,
    size_precision: int,
) -> Iterator[Bar]:
    for row in rows:
        yield parse_kline_row(row, bar_type, price_precision, size_precision)


def parse_funding_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a Binance funding-rate REST entry to the local Parquet schema.

    Input keys (per Binance docs): ``symbol``, ``fundingTime`` (ms), ``fundingRate``
    (string decimal), ``markPrice`` (string decimal).
    """
    return {
        "ts_ns": int(entry["fundingTime"]) * MS_TO_NS,
        "funding_rate": float(entry["fundingRate"]),
        "mark_price": float(entry["markPrice"]),
        "symbol": str(entry["symbol"]),
    }
