from nautilus_trader.model import Bar, BarType, Price, Quantity

from nautilus_runner.data.integrity import check_bars

BAR_TYPE = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
MINUTE_NS = 60 * 1_000_000_000


def _bar(ts_ns: int, close: float = 50000.0) -> Bar:
    p = Price(close, 2)
    return Bar(BAR_TYPE, p, p, p, p, Quantity(1.0, 3), ts_ns, ts_ns + MINUTE_NS - 1)


def test_check_bars_passes_on_contiguous_sequence():
    bars = [_bar(i * MINUTE_NS) for i in range(1, 11)]
    report = check_bars(bars, interval_ns=MINUTE_NS, expected_row_count=10)
    assert report.ok
    assert report.gaps == []
    assert report.duplicates == []
    assert report.price_anomalies == []
    assert report.non_positive_prices == []


def test_check_bars_flags_gap():
    ts = [1, 2, 4, 5]  # missing 3
    bars = [_bar(i * MINUTE_NS) for i in ts]
    report = check_bars(bars, interval_ns=MINUTE_NS)
    assert not report.ok
    assert report.gaps == [(2 * MINUTE_NS, 4 * MINUTE_NS)]


def test_check_bars_flags_duplicate():
    ts = [1, 2, 2, 3]
    bars = [_bar(i * MINUTE_NS) for i in ts]
    report = check_bars(bars, interval_ns=MINUTE_NS)
    assert not report.ok
    assert report.duplicates == [2 * MINUTE_NS]


def test_check_bars_flags_price_anomaly():
    bars = [
        _bar(1 * MINUTE_NS, close=50000.0),
        _bar(2 * MINUTE_NS, close=50100.0),
        _bar(3 * MINUTE_NS, close=1.0),  # >10x drop
        _bar(4 * MINUTE_NS, close=1.01),
    ]
    report = check_bars(bars, interval_ns=MINUTE_NS, max_bar_to_bar_ratio=10.0)
    assert not report.ok
    assert len(report.price_anomalies) == 1
    assert report.price_anomalies[0]["close"] == 1.0


def test_check_bars_flags_non_positive_price():
    zero = Price(0.0, 2)
    q = Quantity(1.0, 3)
    bars = [
        _bar(1 * MINUTE_NS),
        Bar(BAR_TYPE, zero, zero, zero, zero, q, 2 * MINUTE_NS, 3 * MINUTE_NS - 1),
    ]
    report = check_bars(bars, interval_ns=MINUTE_NS)
    assert not report.ok
    assert report.non_positive_prices == [2 * MINUTE_NS]


def test_check_bars_flags_row_count_mismatch():
    bars = [_bar(i * MINUTE_NS) for i in range(1, 6)]
    report = check_bars(bars, interval_ns=MINUTE_NS, expected_row_count=10)
    assert not report.ok
    assert report.row_count == 5
    assert report.expected_row_count == 10


def test_to_dict_includes_ok_flag():
    bars = [_bar(i * MINUTE_NS) for i in range(1, 4)]
    report = check_bars(bars, interval_ns=MINUTE_NS)
    d = report.to_dict()
    assert d["ok"] is True
    assert d["row_count"] == 3
    assert d["gaps"] == []
