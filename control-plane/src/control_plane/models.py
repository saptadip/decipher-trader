from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class StrategyStatus(str, enum.Enum):
    draft = "draft"
    backtest = "backtest"
    paper = "paper"
    live = "live"
    retired = "retired"


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    code_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[StrategyStatus] = mapped_column(Enum(StrategyStatus), default=StrategyStatus.draft)
    capital_weight: Mapped[float] = mapped_column(Float, default=0.0)
    max_notional: Mapped[float] = mapped_column(Float)
    max_daily_loss: Mapped[float] = mapped_column(Float)
    max_position: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    backtest_metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    metrics: Mapped[list["MetricsSnapshot"]] = relationship(back_populates="strategy", cascade="all,delete-orphan")


class MetricsSnapshot(Base):
    __tablename__ = "metrics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    pnl: Mapped[float] = mapped_column(Float)
    sharpe: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    n_trades: Mapped[int] = mapped_column(Integer)

    strategy: Mapped[Strategy] = relationship(back_populates="metrics")


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(200), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
