"""Structured result of a single backtest run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class BacktestSummary:
    bar_type: str
    start: datetime
    end: datetime
    n_bars: int
    initial_balance: Decimal
    final_balance: Decimal
    realized_pnl_total: Decimal
    n_trades: int
    sharpe: float
    max_drawdown: float
    raw_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        data["initial_balance"] = str(self.initial_balance)
        data["final_balance"] = str(self.final_balance)
        data["realized_pnl_total"] = str(self.realized_pnl_total)
        return data
