"""Integrity checks for a sequence of Nautilus Bar objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from nautilus_trader.model import Bar

NS_PER_MS = 1_000_000


@dataclass
class IntegrityReport:
    interval_ns: int
    row_count: int
    expected_row_count: int | None
    gaps: list[tuple[int, int]] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    price_anomalies: list[dict[str, Any]] = field(default_factory=list)
    non_positive_prices: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if self.gaps or self.duplicates or self.price_anomalies or self.non_positive_prices:
            return False
        if self.expected_row_count is not None and self.row_count != self.expected_row_count:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


def check_bars(
    bars: list[Bar],
    interval_ns: int,
    expected_row_count: int | None = None,
    max_bar_to_bar_ratio: float = 10.0,
) -> IntegrityReport:
    """Verify a bar sequence for gaps, duplicates and price sanity.

    ``interval_ns``: the bar step in nanoseconds (60_000_000_000 for 1m).
    ``max_bar_to_bar_ratio``: flag if abs(prev_close / close) or its reciprocal exceeds this.
    """
    report = IntegrityReport(
        interval_ns=interval_ns,
        row_count=len(bars),
        expected_row_count=expected_row_count,
    )

    prev_ts: int | None = None
    prev_close: float | None = None

    for bar in bars:
        ts = bar.ts_event
        o = float(bar.open)
        h = float(bar.high)
        l = float(bar.low)  # noqa: E741
        c = float(bar.close)

        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            report.non_positive_prices.append(ts)

        if prev_ts is not None:
            delta = ts - prev_ts
            if delta == 0:
                report.duplicates.append(ts)
            elif delta != interval_ns:
                report.gaps.append((prev_ts, ts))

        if prev_close is not None and prev_close > 0 and c > 0:
            ratio = max(c / prev_close, prev_close / c)
            if ratio > max_bar_to_bar_ratio:
                report.price_anomalies.append(
                    {"ts_ns": ts, "prev_close": prev_close, "close": c, "ratio": ratio},
                )

        prev_ts = ts
        prev_close = c

    return report
