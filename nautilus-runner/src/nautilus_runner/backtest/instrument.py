"""Factory for the Binance BTCUSDT-PERP instrument used by the backtest runner.

Matches the ``BTCUSDT-PERP.BINANCE`` bar_type prefix that
``scripts/download_data.py`` writes into the Parquet catalog by default
(``price_precision=2``, ``size_precision=3``). Fee defaults (18 bps taker,
20 bps maker) mirror Binance USDM tier-0 published fees; ``MakerTakerFeeModel``
reads these off the instrument at fill time.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model import (
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Money,
    Price,
    Quantity,
    Symbol,
    Venue,
)

BINANCE = Venue("BINANCE")


def build_btcusdt_perp(
    *,
    price_precision: int = 2,
    size_precision: int = 3,
    maker_fee: Decimal = Decimal("0.000200"),
    taker_fee: Decimal = Decimal("0.000180"),
    margin_init: Decimal = Decimal("0.0500"),
    margin_maint: Decimal = Decimal("0.0250"),
) -> CryptoPerpetual:
    """Return a ``BTCUSDT-PERP.BINANCE`` ``CryptoPerpetual`` for backtests."""
    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), BINANCE),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=Price(10.0 ** -price_precision, price_precision),
        size_increment=Quantity(10.0 ** -size_precision, size_precision),
        ts_event=0,
        ts_init=0,
        max_quantity=Quantity(1000.0, size_precision),
        min_quantity=Quantity(10.0 ** -size_precision, size_precision),
        min_notional=Money(10.00, usdt),
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )
