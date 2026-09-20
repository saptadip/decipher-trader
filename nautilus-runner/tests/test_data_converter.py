from nautilus_trader.model import BarType

from nautilus_runner.data.converter import (
    MS_TO_NS,
    parse_funding_entry,
    parse_kline_row,
    parse_kline_rows,
)

BAR_TYPE = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")


def _row(open_ms: int, close_ms: int, o: str, h: str, l: str, c: str, v: str) -> list[str]:
    return [
        str(open_ms),
        o,
        h,
        l,
        c,
        v,
        str(close_ms),
        "0",
        "1",
        "0",
        "0",
        "0",
    ]


def test_parse_kline_row_maps_all_fields():
    row = _row(1_700_000_000_000, 1_700_000_059_999, "50000.10", "50010.50", "49990.00", "50005.25", "1.234")
    bar = parse_kline_row(row, BAR_TYPE, price_precision=2, size_precision=3)

    assert bar.bar_type == BAR_TYPE
    assert bar.ts_event == 1_700_000_000_000 * MS_TO_NS
    assert bar.ts_init == 1_700_000_059_999 * MS_TO_NS
    assert str(bar.open) == "50000.10"
    assert str(bar.high) == "50010.50"
    assert str(bar.low) == "49990.00"
    assert str(bar.close) == "50005.25"
    assert str(bar.volume) == "1.234"
    assert bar.open.precision == 2
    assert bar.volume.precision == 3


def test_parse_kline_row_rejects_short_row():
    try:
        parse_kline_row(["1", "2", "3"], BAR_TYPE, 2, 3)
    except ValueError as exc:
        assert "Kline row must have >= 7 columns" in str(exc)
    else:
        raise AssertionError("expected ValueError for short row")


def test_parse_kline_rows_streams_bars_in_order():
    rows = [
        _row(1_700_000_000_000, 1_700_000_059_999, "50000.00", "50000.00", "50000.00", "50000.00", "0.1"),
        _row(1_700_000_060_000, 1_700_000_119_999, "50000.00", "50001.00", "50000.00", "50001.00", "0.2"),
    ]
    bars = list(parse_kline_rows(rows, BAR_TYPE, 2, 3))
    assert len(bars) == 2
    assert bars[0].ts_event < bars[1].ts_event
    assert str(bars[1].close) == "50001.00"


def test_parse_funding_entry_maps_fields():
    entry = {
        "symbol": "BTCUSDT",
        "fundingTime": 1_700_000_000_000,
        "fundingRate": "0.00012345",
        "markPrice": "50000.15",
    }
    out = parse_funding_entry(entry)
    assert out["ts_ns"] == 1_700_000_000_000 * MS_TO_NS
    assert out["funding_rate"] == 0.00012345
    assert out["mark_price"] == 50000.15
    assert out["symbol"] == "BTCUSDT"
