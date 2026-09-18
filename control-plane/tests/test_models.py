from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from control_plane.db import get_engine, get_sessionmaker
from control_plane.models import Base, AuditEntry, MetricsSnapshot, Strategy, StrategyStatus


def test_can_insert_and_read_strategy(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    with Session() as s:
        s.add(
            Strategy(
                name="toy",
                code_path="strategies/toy/strategy.py",
                status=StrategyStatus.draft,
                capital_weight=0.0,
                max_notional=100.0,
                max_daily_loss=10.0,
                max_position=1.0,
            )
        )
        s.commit()

    with Session() as s:
        row = s.scalars(select(Strategy).where(Strategy.name == "toy")).one()
        assert row.status is StrategyStatus.draft


def test_audit_and_metrics_tables_exist(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    now = datetime.now(timezone.utc)
    with Session() as s:
        # A parent strategy is required for the MetricsSnapshot FK.
        s.add(
            Strategy(
                name="audit-test-strat",
                code_path="strategies/audit/strategy.py",
                status=StrategyStatus.draft,
                capital_weight=0.0,
                max_notional=100.0,
                max_daily_loss=10.0,
                max_position=1.0,
            )
        )
        s.flush()  # assigns id=1
        s.add(AuditEntry(ts=now, actor="operator", action="ping", payload_json="{}"))
        s.add(
            MetricsSnapshot(
                strategy_id=1,
                ts=now,
                pnl=0.0,
                sharpe=0.0,
                max_drawdown=0.0,
                n_trades=0,
            )
        )
        s.commit()

    with Session() as s:
        assert s.scalars(select(AuditEntry)).one().action == "ping"
        assert s.scalars(select(MetricsSnapshot)).one().strategy_id == 1
