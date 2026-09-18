# decipher-trader Phase 1 — Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the end-to-end pipeline that lets a human operator run a hand-written trading strategy on Hyperliquid testnet, watch its metrics, and (through a hard-gated dashboard flow) promote it to Hyperliquid mainnet, with a global kill switch. No LLM yet.

**Architecture:** Three containers on one docker-compose network. `control-plane` (FastAPI) owns a SQLite database and exposes REST + WebSocket. `nautilus-runner` boots a `LiveNode` with the Hyperliquid data + exec clients and runs strategies it fetches from control-plane. `dashboard` (Next.js) is the operator UI and talks only to control-plane. All state lives in named docker volumes; nothing is host-specific.

**Tech Stack:** Python 3.12 + uv + FastAPI + SQLAlchemy 2 + Alembic + SQLite (WAL) + Pydantic v2 for control-plane; Python 3.12 + uv + `nautilus-trader==2.0.0rc6` for nautilus-runner; Next.js 15 (App Router, TypeScript, React 19, `iron-session` for cookies, native `fetch`) for dashboard; pytest, Playwright, docker compose v2.

**Spec:** `docs/superpowers/specs/2026-09-18-decipher-trader-design.md`

## Global Constraints

- **Never modify upstream `nautilus_trader/`.** Consume it as a pinned pip dependency (`nautilus-trader==2.0.0rc6`). Upgrade path: bump the pin, run the test suite, do not fork.
- **Portability.** Every path used inside a container is under `/app/...`. Every configurable value comes from `.env`. Persistent state lives in named docker volumes (`decipher-db`, `decipher-cache`). No host paths in any code.
- **Trading modes.** `TRADING_MODE ∈ {paper, live}`. `paper` maps to `HyperliquidEnvironment.TESTNET`. `live` maps to `HyperliquidEnvironment.MAINNET`. Live credentials must not be present in the environment unless `TRADING_MODE=live`.
- **Safety gates implemented in Phase 1:** G1 (nautilus-runner start-up promotion check), G2 (mode-scoped credentials via `docker-compose.override.yml`), G3 (per-strategy risk caps into Nautilus `RiskEngine`), G4 (global kill switch), G5 (append-only audit log), G7 (14-day paper-forward + no fatal drawdown before promote), G8 (`OPERATOR_TOKEN` on every mutation), G9 (heartbeat with 2-miss auto-kill). G6 belongs to Phase 2.
- **Auth.** One `OPERATOR_TOKEN` string in `.env`. Every mutating REST endpoint requires it. Browser sessions are HTTP-only cookies issued by the dashboard container; the dashboard attaches `OPERATOR_TOKEN` server-side to every outbound call.
- **Commits.** No Conventional Commits prefix. No issue/PR number in the subject. Plain imperative subject, optional body.
- **Testing baseline.** Every task ends with a passing test suite for the subsystem it touched. Never weaken or delete a test to make CI green.
- **Package management.** Every Python service uses `uv` with its own `pyproject.toml`. Every service has its own `.venv` inside its own directory (e.g. `control-plane/.venv`).
- **Line width.** Python: 100 columns, `ruff format` defaults. TypeScript: 100 columns, Prettier defaults.

---

## File Structure

```
decipher-trader/
├── .env.example
├── .gitignore
├── README.md
├── docker-compose.yml
├── docker-compose.paper.yml           # override: paper-only, no live keys
├── docker-compose.live.yml            # override: live keys mounted
├── docs/
│   └── superpowers/
│       ├── specs/2026-09-18-decipher-trader-design.md
│       └── plans/2026-09-18-phase-1-pipeline.md
├── control-plane/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 0001_initial.py
│   ├── src/control_plane/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI app + startup
│   │   ├── config.py                  # env parsing
│   │   ├── db.py                      # engine + session
│   │   ├── models.py                  # SQLAlchemy ORM
│   │   ├── schemas.py                 # Pydantic in/out
│   │   ├── auth.py                    # OPERATOR_TOKEN dep
│   │   ├── events.py                  # WS broadcaster
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── health.py
│   │       ├── strategies.py
│   │       ├── kill.py
│   │       ├── metrics.py
│   │       └── audit.py
│   └── tests/
│       ├── conftest.py
│       ├── test_health.py
│       ├── test_auth.py
│       ├── test_strategies.py
│       ├── test_promote.py
│       ├── test_kill.py
│       ├── test_metrics.py
│       ├── test_audit.py
│       └── test_events_ws.py
├── nautilus-runner/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/nautilus_runner/
│   │   ├── __init__.py
│   │   ├── main.py                    # LiveNode boot
│   │   ├── config.py                  # env + control-plane fetch (G1, G2, G3)
│   │   ├── control_plane_client.py    # HTTP + WS client
│   │   ├── heartbeat.py               # G9
│   │   └── kill_listener.py           # G4 consumer
│   ├── strategies/
│   │   └── toy_momentum/
│   │       ├── __init__.py
│   │       └── strategy.py
│   └── tests/
│       ├── test_config.py
│       ├── test_control_plane_client.py
│       ├── test_toy_momentum.py
│       └── test_heartbeat.py
├── dashboard/
│   ├── package.json
│   ├── next.config.js
│   ├── tsconfig.json
│   ├── Dockerfile
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx               # / strategy list
│   │   │   ├── login/page.tsx
│   │   │   ├── strategy/[id]/page.tsx
│   │   │   ├── audit/page.tsx
│   │   │   ├── settings/page.tsx
│   │   │   └── api/
│   │   │       ├── auth/route.ts       # POST login, DELETE logout
│   │   │       └── control/[...path]/route.ts  # proxy
│   │   ├── lib/
│   │   │   ├── controlPlane.ts        # server-side fetch wrapper
│   │   │   └── session.ts             # iron-session helpers
│   │   └── components/
│   │       ├── StatusBadge.tsx
│   │       ├── PnLSparkline.tsx
│   │       └── KillSwitch.tsx
│   └── tests/
│       ├── controlPlane.test.ts
│       ├── session.test.ts
│       └── e2e/
│           └── promote.spec.ts        # Playwright
└── e2e/
    ├── docker-compose.e2e.yml         # brings up all three + a fixture mock of Hyperliquid
    ├── fixtures/
    │   └── hyperliquid_ws.json
    ├── mock_hyperliquid.py            # tiny FastAPI mock serving fixtures
    └── smoke.py                       # driver script asserting the full flow
```

---

## Task Index

1. Repo skeleton and top-level compose stub
2. `control-plane` bootstrap and `/health`
3. `control-plane` DB models and initial Alembic migration
4. `control-plane` operator-token auth dependency
5. `control-plane` `/strategies` list + create
6. `control-plane` `/strategies/{id}/start_paper`, `/promote` (G7), and `/demote`
7. `control-plane` `/kill_all` (G4)
8. `control-plane` `/metrics/{strategy_id}`
9. `control-plane` `/audit` GET + POST (G5)
10. `control-plane` `WS /events` broadcaster
11. `control-plane` Dockerfile + compose entry
12. `nautilus-runner` bootstrap and control-plane HTTP client
13. `nautilus-runner` config builder with G1 + G2 + G3
14. Toy momentum `Strategy` subclass + backtest unit test
15. `nautilus-runner` LiveNode entrypoint
16. `nautilus-runner` heartbeat (G9) + kill listener
17. `nautilus-runner` Dockerfile + compose entry
18. `dashboard` Next.js scaffold + login + session
19. `dashboard` server-side control-plane client + `/api/control/*` proxy
20. `dashboard` strategy list + detail + promote/demote buttons
21. `dashboard` audit page + settings page + global kill switch
22. `dashboard` Dockerfile + compose entry
23. Root docker-compose + overrides + `.env.example` + operator runbook
24. End-to-end smoke test with mocked Hyperliquid

---

### Task 1: Repo skeleton and top-level compose stub

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: repo layout that later tasks can populate; `.env.example` schema every service will read.

- [ ] **Step 1: Create `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
*.egg-info/

# Node
node_modules/
.next/
out/
coverage/

# SQLite scratch
*.sqlite3
*.sqlite3-journal
*.sqlite3-wal
*.sqlite3-shm

# Env
.env
.env.local
!.env.example

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 2: Create `.env.example`**

```
# Trading mode: paper or live
TRADING_MODE=paper

# Control-plane
OPERATOR_TOKEN=change-me-to-a-long-random-string
CONTROL_PLANE_URL=http://control-plane:8000
CONTROL_PLANE_DB_PATH=/app/data/decipher.sqlite3

# Dashboard
DASHBOARD_SESSION_SECRET=change-me-to-a-32-plus-char-random-string
DASHBOARD_OPERATOR_USERNAME=operator
DASHBOARD_OPERATOR_PASSWORD_BCRYPT=change-me-to-a-bcrypt-hash

# Hyperliquid — testnet fields safe to set in paper mode
HYPERLIQUID_TESTNET_PRIVATE_KEY=
HYPERLIQUID_TESTNET_ACCOUNT_ID=HYPERLIQUID-TESTNET-001

# Hyperliquid — mainnet fields; leave empty unless TRADING_MODE=live
HYPERLIQUID_MAINNET_PRIVATE_KEY=
HYPERLIQUID_MAINNET_ACCOUNT_ID=

# Nautilus-runner
NAUTILUS_TRADER_ID=DECIPHER-001
HEARTBEAT_INTERVAL_SECS=30
HEARTBEAT_MISS_LIMIT=2

# Promotion thresholds (see spec §5)
PAPER_FORWARD_MIN_DAYS=14
```

- [ ] **Step 3: Create `README.md` with a minimal operator overview**

```markdown
# decipher-trader

Self-hosted crypto trading system built on NautilusTrader and Hyperliquid.

## Quick start (paper mode)

1. Copy `.env.example` to `.env` and fill in the values.
2. `docker compose -f docker-compose.yml -f docker-compose.paper.yml up --build`
3. Visit `http://localhost:3000`.

Detailed operator runbook lives in `README.md` after Task 23.
```

- [ ] **Step 4: Create `docker-compose.yml` stub**

```yaml
name: decipher-trader

networks:
  decipher:
    driver: bridge

volumes:
  decipher-db:
  decipher-cache:

services: {}
```

- [ ] **Step 5: Commit**

```bash
git init
git add .gitignore .env.example README.md docker-compose.yml
git commit -m "Add repo skeleton"
```

---

### Task 2: `control-plane` bootstrap and `/health`

**Files:**
- Create: `control-plane/pyproject.toml`
- Create: `control-plane/src/control_plane/__init__.py`
- Create: `control-plane/src/control_plane/main.py`
- Create: `control-plane/src/control_plane/config.py`
- Create: `control-plane/src/control_plane/routers/__init__.py`
- Create: `control-plane/src/control_plane/routers/health.py`
- Create: `control-plane/tests/conftest.py`
- Create: `control-plane/tests/test_health.py`

**Interfaces:**
- Consumes: `.env.example` keys `OPERATOR_TOKEN`, `CONTROL_PLANE_DB_PATH`.
- Produces:
  - `control_plane.main.app` — FastAPI app.
  - `control_plane.config.Settings` — Pydantic settings object with `operator_token: str`, `db_path: Path`, `paper_forward_min_days: int`.
  - `GET /health` → `{"status": "ok"}`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "control-plane"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi==0.115.0",
  "uvicorn[standard]==0.32.0",
  "sqlalchemy==2.0.35",
  "alembic==1.13.3",
  "pydantic==2.9.2",
  "pydantic-settings==2.5.2",
  "bcrypt==4.2.0",
  "python-multipart==0.0.12",
]

[project.optional-dependencies]
dev = [
  "pytest==8.3.3",
  "pytest-asyncio==0.24.0",
  "httpx==0.27.2",
  "ruff==0.6.9",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/control_plane"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Bootstrap the venv**

Run:
```bash
cd control-plane
uv venv
uv pip install -e ".[dev]"
```

- [ ] **Step 3: Write the failing test**

Create `control-plane/tests/conftest.py`:
```python
import pytest
from fastapi.testclient import TestClient

from control_plane.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
```

Create `control-plane/tests/test_health.py`:
```python
def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_health.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'control_plane'` (module not created yet).

- [ ] **Step 5: Create the minimal implementation**

Create `control-plane/src/control_plane/__init__.py` (empty).

Create `control-plane/src/control_plane/config.py`:
```python
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", populate_by_name=True)

    operator_token: str = Field(default="dev-token", alias="OPERATOR_TOKEN")
    db_path: Path = Field(default=Path("/app/data/decipher.sqlite3"), alias="CONTROL_PLANE_DB_PATH")
    paper_forward_min_days: int = Field(default=14, alias="PAPER_FORWARD_MIN_DAYS")


def get_settings() -> Settings:
    return Settings()
```

Create `control-plane/src/control_plane/routers/__init__.py` (empty).

Create `control-plane/src/control_plane/routers/health.py`:
```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

Create `control-plane/src/control_plane/main.py`:
```python
from fastapi import FastAPI

from control_plane.routers import health

app = FastAPI(title="decipher-trader control-plane")
app.include_router(health.router)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd control-plane && uv run pytest tests/test_health.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add control-plane/pyproject.toml \
        control-plane/src/control_plane/__init__.py \
        control-plane/src/control_plane/main.py \
        control-plane/src/control_plane/config.py \
        control-plane/src/control_plane/routers/__init__.py \
        control-plane/src/control_plane/routers/health.py \
        control-plane/tests/conftest.py \
        control-plane/tests/test_health.py
git commit -m "Bootstrap control-plane with health endpoint"
```

---

### Task 3: `control-plane` DB models and initial Alembic migration

**Files:**
- Create: `control-plane/src/control_plane/db.py`
- Create: `control-plane/src/control_plane/models.py`
- Create: `control-plane/alembic.ini`
- Create: `control-plane/alembic/env.py`
- Create: `control-plane/alembic/script.py.mako`
- Create: `control-plane/alembic/versions/0001_initial.py`
- Create: `control-plane/tests/test_models.py`

**Interfaces:**
- Consumes: `Settings.db_path`.
- Produces:
  - `control_plane.db.get_engine()` → SQLAlchemy `Engine` with WAL enabled.
  - `control_plane.db.get_session()` → FastAPI dependency yielding `Session`.
  - `control_plane.models.Base` — declarative base.
  - `Strategy`, `MetricsSnapshot`, `AuditEntry` ORM classes with fields exactly as listed below.

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_models.py`:
```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_models.py -v`

Expected: FAIL — `ModuleNotFoundError` for `control_plane.db` and `control_plane.models`.

- [ ] **Step 3: Create the models**

Create `control-plane/src/control_plane/models.py`:
```python
from __future__ import annotations

import enum
from datetime import datetime

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.utcnow())
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
```

Create `control-plane/src/control_plane/db.py`:
```python
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from control_plane.config import get_settings


def _enable_wal(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


def get_engine(db_path: Path | None = None) -> Engine:
    settings = get_settings()
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", future=True)
    _enable_wal(engine)
    return engine


def get_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def get_session():
    global _engine, _Session
    if _engine is None:
        _engine = get_engine()
        _Session = get_sessionmaker(_engine)
    assert _Session is not None
    with _Session() as session:
        yield session
```

- [ ] **Step 4: Run the test to verify models pass**

Run: `cd control-plane && uv run pytest tests/test_models.py -v`

Expected: PASS.

- [ ] **Step 5: Initialize Alembic**

Run:
```bash
cd control-plane
uv run alembic init -t generic alembic
```

Overwrite `control-plane/alembic.ini` with:
```ini
[alembic]
script_location = alembic
sqlalchemy.url = sqlite:////app/data/decipher.sqlite3

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Overwrite `control-plane/alembic/env.py` with:
```python
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from control_plane.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 6: Autogenerate the initial migration**

Run:
```bash
cd control-plane
mkdir -p /tmp/decipher-mig && \
CONTROL_PLANE_DB_PATH=/tmp/decipher-mig/mig.sqlite3 \
uv run alembic revision --autogenerate -m "initial schema" --rev-id 0001
```

Rename the generated file to `control-plane/alembic/versions/0001_initial.py` if the autogen used a different filename. Open it and confirm it creates the three tables. Delete `/tmp/decipher-mig`.

- [ ] **Step 7: Verify migration runs cleanly**

```bash
cd control-plane
rm -f /tmp/decipher-check.sqlite3
CONTROL_PLANE_DB_PATH=/tmp/decipher-check.sqlite3 uv run alembic upgrade head
CONTROL_PLANE_DB_PATH=/tmp/decipher-check.sqlite3 uv run python -c "from sqlalchemy import inspect; from control_plane.db import get_engine; print(sorted(inspect(get_engine()).get_table_names()))"
```

Expected output includes: `alembic_version`, `audit_log`, `metrics_snapshots`, `strategies`.

- [ ] **Step 8: Commit**

```bash
git add control-plane/src/control_plane/db.py \
        control-plane/src/control_plane/models.py \
        control-plane/alembic.ini \
        control-plane/alembic/env.py \
        control-plane/alembic/script.py.mako \
        control-plane/alembic/versions/0001_initial.py \
        control-plane/tests/test_models.py
git commit -m "Add strategy, metrics, and audit models with initial migration"
```

---

### Task 4: `control-plane` operator-token auth dependency

**Files:**
- Create: `control-plane/src/control_plane/auth.py`
- Create: `control-plane/tests/test_auth.py`

**Interfaces:**
- Consumes: `Settings.operator_token`.
- Produces: `control_plane.auth.require_operator` — FastAPI dependency that raises 401 if the request lacks a matching `Authorization: Bearer <token>` header; returns the actor label `"operator"` on success.

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_auth.py`:
```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from control_plane.auth import require_operator
from control_plane.config import get_settings, Settings


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/needs-auth")
    async def needs_auth(actor: str = Depends(require_operator)) -> dict[str, str]:
        return {"actor": actor}

    return app


def test_missing_token_returns_401():
    app = _make_app()
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret")
    client = TestClient(app)
    resp = client.get("/needs-auth")
    assert resp.status_code == 401


def test_wrong_token_returns_401():
    app = _make_app()
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret")
    client = TestClient(app)
    resp = client.get("/needs-auth", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_correct_token_returns_actor():
    app = _make_app()
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret")
    client = TestClient(app)
    resp = client.get("/needs-auth", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200
    assert resp.json() == {"actor": "operator"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_auth.py -v`

Expected: FAIL — `ImportError` for `control_plane.auth`.

- [ ] **Step 3: Implement the auth dependency**

Create `control-plane/src/control_plane/auth.py`:
```python
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from control_plane.config import Settings, get_settings


async def require_operator(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.operator_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return "operator"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd control-plane && uv run pytest tests/test_auth.py -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add control-plane/src/control_plane/auth.py control-plane/tests/test_auth.py
git commit -m "Require operator bearer token on protected endpoints"
```

---

### Task 5: `control-plane` `/strategies` list + create

**Files:**
- Create: `control-plane/src/control_plane/schemas.py`
- Create: `control-plane/src/control_plane/routers/strategies.py`
- Modify: `control-plane/src/control_plane/main.py`
- Create: `control-plane/tests/test_strategies.py`

**Interfaces:**
- Consumes: `require_operator`, `get_session`.
- Produces:
  - `GET /strategies?status=<csv>` → `list[StrategyOut]`. No auth needed (internal reads).
  - `POST /strategies` → creates one in `draft`, returns `StrategyOut`. Auth required.
  - `StrategyOut` and `StrategyCreate` schemas (see below).

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_strategies.py`:
```python
import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_session, get_engine, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _session():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret", db_path=db_path)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer s3cret"}


def test_list_strategies_empty(client):
    resp = client.get("/strategies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_requires_auth(client):
    resp = client.post("/strategies", json={"name": "x", "code_path": "y", "max_notional": 1, "max_daily_loss": 1, "max_position": 1})
    assert resp.status_code == 401


def test_create_returns_draft(client):
    resp = client.post(
        "/strategies",
        json={"name": "toy", "code_path": "strategies/toy/strategy.py", "max_notional": 100, "max_daily_loss": 10, "max_position": 1},
        headers=_auth(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "toy"
    assert body["status"] == "draft"

    resp2 = client.get("/strategies?status=draft")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_strategies.py -v`

Expected: FAIL — module `control_plane.routers.strategies` does not exist.

- [ ] **Step 3: Implement schemas and router**

Create `control-plane/src/control_plane/schemas.py`:
```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from control_plane.models import StrategyStatus


class StrategyCreate(BaseModel):
    name: str
    code_path: str
    max_notional: float = Field(gt=0)
    max_daily_loss: float = Field(gt=0)
    max_position: float = Field(gt=0)
    capital_weight: float = 0.0


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code_path: str
    status: StrategyStatus
    capital_weight: float
    max_notional: float
    max_daily_loss: float
    max_position: float
    created_at: datetime
    paper_started_at: datetime | None
    promoted_at: datetime | None
    promoted_by: str | None
    retired_at: datetime | None


class PromoteRequest(BaseModel):
    confirm: bool = True


class MetricsSnapshotIn(BaseModel):
    ts: datetime
    pnl: float
    sharpe: float
    max_drawdown: float
    n_trades: int


class MetricsSnapshotOut(MetricsSnapshotIn):
    model_config = ConfigDict(from_attributes=True)
    strategy_id: int


class AuditEntryIn(BaseModel):
    actor: str
    action: str
    payload_json: str


class AuditEntryOut(AuditEntryIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
```

Create `control-plane/src/control_plane/routers/strategies.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.models import Strategy, StrategyStatus
from control_plane.schemas import StrategyCreate, StrategyOut

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyOut])
async def list_strategies(
    status_csv: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
) -> list[Strategy]:
    stmt = select(Strategy)
    if status_csv:
        wanted = [StrategyStatus(s.strip()) for s in status_csv.split(",") if s.strip()]
        stmt = stmt.where(Strategy.status.in_(wanted))
    return list(session.scalars(stmt).all())


@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    payload: StrategyCreate,
    session: Session = Depends(get_session),
    _actor: str = Depends(require_operator),
) -> Strategy:
    existing = session.scalar(select(Strategy).where(Strategy.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="strategy name already exists")

    strategy = Strategy(
        name=payload.name,
        code_path=payload.code_path,
        status=StrategyStatus.draft,
        capital_weight=payload.capital_weight,
        max_notional=payload.max_notional,
        max_daily_loss=payload.max_daily_loss,
        max_position=payload.max_position,
        created_at=datetime.now(timezone.utc),
    )
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return strategy
```

Modify `control-plane/src/control_plane/main.py`:
```python
from fastapi import FastAPI

from control_plane.routers import health, strategies

app = FastAPI(title="decipher-trader control-plane")
app.include_router(health.router)
app.include_router(strategies.router)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd control-plane && uv run pytest tests/test_strategies.py -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add control-plane/src/control_plane/schemas.py \
        control-plane/src/control_plane/routers/strategies.py \
        control-plane/src/control_plane/main.py \
        control-plane/tests/test_strategies.py
git commit -m "Add list and create endpoints for strategies"
```

---

### Task 6: `control-plane` `/strategies/{id}/start_paper`, `/promote` (G7), and `/demote`

**Files:**
- Modify: `control-plane/src/control_plane/routers/strategies.py`
- Create: `control-plane/tests/test_promote.py`

**Interfaces:**
- Consumes: `require_operator`, `get_session`, `Settings.paper_forward_min_days`.
- Produces:
  - `POST /strategies/{id}/start_paper` — flips `draft` → `paper`, sets `paper_started_at=now`. Required in Phase 1 because the automated `draft→backtest→paper` pipeline lives in Phase 2. Rejects with 409 if strategy is not `draft`.
  - `POST /strategies/{id}/promote` — flips `paper` → `live`, sets `promoted_at`, `promoted_by`, writes audit entry. Rejects with 409 if strategy is not `paper`, if `paper_started_at` is missing or too recent, or if any snapshot in the last `paper_forward_min_days` days shows `max_drawdown > max_daily_loss`.
  - `POST /strategies/{id}/demote` — flips `live` → `paper`; audit entry written.

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_promote.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import AuditEntry, Base, MetricsSnapshot, Strategy, StrategyStatus


@pytest.fixture()
def env(tmp_path):
    db_path = tmp_path / "t.sqlite3"
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _session():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret", db_path=db_path, paper_forward_min_days=14)
    yield Session, TestClient(app)
    app.dependency_overrides.clear()


AUTH = {"Authorization": "Bearer s3cret"}


def _seed(Session, *, status: StrategyStatus, paper_started_at=None, max_daily_loss=10.0):
    with Session() as s:
        row = Strategy(
            name="toy",
            code_path="p",
            status=status,
            capital_weight=1.0,
            max_notional=100.0,
            max_daily_loss=max_daily_loss,
            max_position=1.0,
            paper_started_at=paper_started_at,
        )
        s.add(row)
        s.commit()
        return row.id


def test_promote_rejects_non_paper(env):
    Session, client = env
    sid = _seed(Session, status=StrategyStatus.draft)
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 409


def test_promote_rejects_too_recent_paper(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=5)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 409
    assert "14" in resp.json()["detail"]


def test_promote_rejects_fatal_drawdown(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=20)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started, max_daily_loss=10.0)
    with Session() as s:
        s.add(
            MetricsSnapshot(
                strategy_id=sid,
                ts=datetime.now(timezone.utc) - timedelta(days=3),
                pnl=-50,
                sharpe=0.0,
                max_drawdown=20.0,
                n_trades=1,
            )
        )
        s.commit()
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 409
    assert "drawdown" in resp.json()["detail"]


def test_promote_success(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=20)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "live"
    assert body["promoted_by"] == "operator"

    with Session() as s:
        rows = s.scalars(select(AuditEntry).where(AuditEntry.action == "promote")).all()
        assert len(rows) == 1


def test_demote_flips_live_to_paper(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=20)
    sid = _seed(Session, status=StrategyStatus.live, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/demote", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paper"


def test_start_paper_from_draft(env):
    Session, client = env
    sid = _seed(Session, status=StrategyStatus.draft)
    resp = client.post(f"/strategies/{sid}/start_paper", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "paper"
    assert body["paper_started_at"] is not None


def test_start_paper_rejects_non_draft(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=1)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/start_paper", headers=AUTH)
    assert resp.status_code == 409
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_promote.py -v`

Expected: 5 FAIL — endpoints not implemented.

- [ ] **Step 3: Implement promote and demote**

Add to `control-plane/src/control_plane/routers/strategies.py` (append below `create_strategy`):
```python
import json
from datetime import timedelta

from control_plane.config import Settings
from control_plane.models import AuditEntry, MetricsSnapshot


def _write_audit(session: Session, actor: str, action: str, payload: dict) -> None:
    session.add(
        AuditEntry(
            ts=datetime.now(timezone.utc),
            actor=actor,
            action=action,
            payload_json=json.dumps(payload, default=str),
        )
    )


from control_plane.config import get_settings


@router.post("/{strategy_id}/promote", response_model=StrategyOut)
async def promote_strategy(
    strategy_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    actor: str = Depends(require_operator),
) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if strategy.status is not StrategyStatus.paper:
        raise HTTPException(status_code=409, detail=f"strategy in status {strategy.status.value}, not paper")
    if strategy.paper_started_at is None:
        raise HTTPException(status_code=409, detail="paper_started_at not set")

    now = datetime.now(timezone.utc)
    min_delta = timedelta(days=settings.paper_forward_min_days)
    if now - strategy.paper_started_at < min_delta:
        raise HTTPException(
            status_code=409,
            detail=f"must run in paper for at least {settings.paper_forward_min_days} days",
        )

    window_start = now - min_delta
    fatal = session.scalar(
        select(MetricsSnapshot)
        .where(MetricsSnapshot.strategy_id == strategy.id)
        .where(MetricsSnapshot.ts >= window_start)
        .where(MetricsSnapshot.max_drawdown > strategy.max_daily_loss)
        .limit(1)
    )
    if fatal is not None:
        raise HTTPException(status_code=409, detail="fatal drawdown snapshot in review window; refuse to promote")

    strategy.status = StrategyStatus.live
    strategy.promoted_at = now
    strategy.promoted_by = actor
    _write_audit(session, actor, "promote", {"strategy_id": strategy.id})
    session.commit()
    session.refresh(strategy)
    return strategy


@router.post("/{strategy_id}/demote", response_model=StrategyOut)
async def demote_strategy(
    strategy_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_operator),
) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if strategy.status is not StrategyStatus.live:
        raise HTTPException(status_code=409, detail=f"strategy in status {strategy.status.value}, not live")

    strategy.status = StrategyStatus.paper
    _write_audit(session, actor, "demote", {"strategy_id": strategy.id})
    session.commit()
    session.refresh(strategy)
    return strategy


@router.post("/{strategy_id}/start_paper", response_model=StrategyOut)
async def start_paper(
    strategy_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_operator),
) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if strategy.status is not StrategyStatus.draft:
        raise HTTPException(status_code=409, detail=f"strategy in status {strategy.status.value}, not draft")
    now = datetime.now(timezone.utc)
    strategy.status = StrategyStatus.paper
    strategy.paper_started_at = now
    _write_audit(session, actor, "start_paper", {"strategy_id": strategy.id})
    session.commit()
    session.refresh(strategy)
    return strategy
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd control-plane && uv run pytest tests/test_promote.py -v`

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add control-plane/src/control_plane/routers/strategies.py control-plane/tests/test_promote.py
git commit -m "Gate promotion on 14-day paper forward and add start_paper transition"
```

---

### Task 7: `control-plane` `/kill_all` (G4)

**Files:**
- Create: `control-plane/src/control_plane/routers/kill.py`
- Modify: `control-plane/src/control_plane/main.py`
- Create: `control-plane/tests/test_kill.py`

**Interfaces:**
- Consumes: `require_operator`, `get_session`.
- Produces: `POST /kill_all` — sets every `status=live` strategy to `paper`; writes one audit entry per demote plus one `kill_all` summary; returns `{"demoted": [ids]}`.

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_kill.py`:
```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import AuditEntry, Base, Strategy, StrategyStatus


@pytest.fixture()
def env(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite3")
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _s():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret", db_path=tmp_path / "t.sqlite3")
    yield Session, TestClient(app)
    app.dependency_overrides.clear()


AUTH = {"Authorization": "Bearer s3cret"}


def test_kill_all_flips_only_live(env):
    Session, client = env
    with Session() as s:
        s.add(Strategy(name="a", code_path="a", status=StrategyStatus.live, capital_weight=1, max_notional=1, max_daily_loss=1, max_position=1))
        s.add(Strategy(name="b", code_path="b", status=StrategyStatus.live, capital_weight=1, max_notional=1, max_daily_loss=1, max_position=1))
        s.add(Strategy(name="c", code_path="c", status=StrategyStatus.paper, capital_weight=1, max_notional=1, max_daily_loss=1, max_position=1))
        s.commit()

    resp = client.post("/kill_all", headers=AUTH)
    assert resp.status_code == 200
    assert set(resp.json()["demoted"]) == {1, 2}

    with Session() as s:
        statuses = {row.name: row.status for row in s.scalars(select(Strategy))}
        assert statuses["a"] is StrategyStatus.paper
        assert statuses["b"] is StrategyStatus.paper
        assert statuses["c"] is StrategyStatus.paper

        actions = [a.action for a in s.scalars(select(AuditEntry)).all()]
        assert actions.count("demote") == 2
        assert actions.count("kill_all") == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_kill.py -v`

Expected: FAIL — router not registered.

- [ ] **Step 3: Implement the router**

Create `control-plane/src/control_plane/routers/kill.py`:
```python
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.models import AuditEntry, Strategy, StrategyStatus

router = APIRouter(tags=["kill"])


@router.post("/kill_all")
async def kill_all(
    session: Session = Depends(get_session),
    actor: str = Depends(require_operator),
) -> dict[str, list[int]]:
    live_rows = list(session.scalars(select(Strategy).where(Strategy.status == StrategyStatus.live)))
    demoted: list[int] = []
    now = datetime.now(timezone.utc)
    for row in live_rows:
        row.status = StrategyStatus.paper
        demoted.append(row.id)
        session.add(
            AuditEntry(
                ts=now,
                actor=actor,
                action="demote",
                payload_json=json.dumps({"strategy_id": row.id, "reason": "kill_all"}),
            )
        )
    session.add(
        AuditEntry(
            ts=now,
            actor=actor,
            action="kill_all",
            payload_json=json.dumps({"demoted": demoted}),
        )
    )
    session.commit()
    return {"demoted": demoted}
```

Modify `control-plane/src/control_plane/main.py`:
```python
from fastapi import FastAPI

from control_plane.routers import health, kill, strategies

app = FastAPI(title="decipher-trader control-plane")
app.include_router(health.router)
app.include_router(strategies.router)
app.include_router(kill.router)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd control-plane && uv run pytest tests/test_kill.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control-plane/src/control_plane/routers/kill.py \
        control-plane/src/control_plane/main.py \
        control-plane/tests/test_kill.py
git commit -m "Add global kill-all endpoint that demotes every live strategy"
```

---

### Task 8: `control-plane` `/metrics/{strategy_id}`

**Files:**
- Create: `control-plane/src/control_plane/routers/metrics.py`
- Modify: `control-plane/src/control_plane/main.py`
- Create: `control-plane/tests/test_metrics.py`

**Interfaces:**
- Consumes: `require_operator`, `get_session`.
- Produces:
  - `GET /metrics/{strategy_id}?since=<iso8601>` → `list[MetricsSnapshotOut]`.
  - `POST /metrics/{strategy_id}` (auth required) — nautilus-runner posts snapshots here.

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_metrics.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base, Strategy, StrategyStatus


@pytest.fixture()
def env(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite3")
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _s():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s", db_path=tmp_path / "t.sqlite3")
    with Session() as s:
        s.add(Strategy(id=1, name="t", code_path="p", status=StrategyStatus.paper, capital_weight=0, max_notional=1, max_daily_loss=1, max_position=1))
        s.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_and_get_metrics(env):
    client = env
    ts = datetime.now(timezone.utc)
    resp = client.post(
        "/metrics/1",
        headers={"Authorization": "Bearer s"},
        json={"ts": ts.isoformat(), "pnl": 1.0, "sharpe": 0.5, "max_drawdown": 0.1, "n_trades": 3},
    )
    assert resp.status_code == 201

    resp = client.get("/metrics/1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pnl"] == 1.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_metrics.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement the router**

Create `control-plane/src/control_plane/routers/metrics.py`:
```python
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.models import MetricsSnapshot, Strategy
from control_plane.schemas import MetricsSnapshotIn, MetricsSnapshotOut

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/{strategy_id}", response_model=list[MetricsSnapshotOut])
async def list_metrics(
    strategy_id: int,
    since: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[MetricsSnapshot]:
    stmt = select(MetricsSnapshot).where(MetricsSnapshot.strategy_id == strategy_id).order_by(MetricsSnapshot.ts.asc())
    if since is not None:
        stmt = stmt.where(MetricsSnapshot.ts >= since)
    return list(session.scalars(stmt).all())


@router.post("/{strategy_id}", response_model=MetricsSnapshotOut, status_code=status.HTTP_201_CREATED)
async def add_metric(
    strategy_id: int,
    payload: MetricsSnapshotIn,
    session: Session = Depends(get_session),
    _actor: str = Depends(require_operator),
) -> MetricsSnapshot:
    if session.get(Strategy, strategy_id) is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    snap = MetricsSnapshot(
        strategy_id=strategy_id,
        ts=payload.ts,
        pnl=payload.pnl,
        sharpe=payload.sharpe,
        max_drawdown=payload.max_drawdown,
        n_trades=payload.n_trades,
    )
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap
```

Update `main.py` to include the router (add `from control_plane.routers import metrics` and `app.include_router(metrics.router)`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd control-plane && uv run pytest tests/test_metrics.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control-plane/src/control_plane/routers/metrics.py \
        control-plane/src/control_plane/main.py \
        control-plane/tests/test_metrics.py
git commit -m "Add metrics ingestion and read endpoints"
```

---

### Task 9: `control-plane` `/audit` GET + POST (G5)

**Files:**
- Create: `control-plane/src/control_plane/routers/audit.py`
- Modify: `control-plane/src/control_plane/main.py`
- Create: `control-plane/tests/test_audit.py`

**Interfaces:**
- Consumes: `require_operator`, `get_session`.
- Produces:
  - `GET /audit?actor=&action=&since=&limit=` → `list[AuditEntryOut]`.
  - `POST /audit` (auth required) — services push audit entries.

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_audit.py`:
```python
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base


@pytest.fixture()
def client(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite3")
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _s():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s", db_path=tmp_path / "t.sqlite3")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_and_filter(client):
    for i, action in enumerate(["order_intent", "fill", "order_intent"]):
        resp = client.post(
            "/audit",
            headers={"Authorization": "Bearer s"},
            json={"actor": "runner", "action": action, "payload_json": f"{{\"i\":{i}}}"},
        )
        assert resp.status_code == 201

    resp = client.get("/audit?action=order_intent")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = client.get("/audit?limit=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_audit.py -v`

Expected: FAIL.

- [ ] **Step 3: Implement the router**

Create `control-plane/src/control_plane/routers/audit.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.models import AuditEntry
from control_plane.schemas import AuditEntryIn, AuditEntryOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def list_audit(
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[AuditEntry]:
    stmt = select(AuditEntry).order_by(AuditEntry.ts.desc())
    if actor:
        stmt = stmt.where(AuditEntry.actor == actor)
    if action:
        stmt = stmt.where(AuditEntry.action == action)
    if since:
        stmt = stmt.where(AuditEntry.ts >= since)
    stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


@router.post("", response_model=AuditEntryOut, status_code=status.HTTP_201_CREATED)
async def add_audit(
    payload: AuditEntryIn,
    session: Session = Depends(get_session),
    _actor: str = Depends(require_operator),
) -> AuditEntry:
    entry = AuditEntry(
        ts=datetime.now(timezone.utc),
        actor=payload.actor,
        action=payload.action,
        payload_json=payload.payload_json,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry
```

Add `from control_plane.routers import audit` and `app.include_router(audit.router)` to `main.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd control-plane && uv run pytest tests/test_audit.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control-plane/src/control_plane/routers/audit.py \
        control-plane/src/control_plane/main.py \
        control-plane/tests/test_audit.py
git commit -m "Expose audit log with append-only insert and filtered read"
```

---

### Task 10: `control-plane` `WS /events` broadcaster

**Files:**
- Create: `control-plane/src/control_plane/events.py`
- Modify: `control-plane/src/control_plane/main.py`
- Modify: `control-plane/src/control_plane/routers/kill.py`
- Modify: `control-plane/src/control_plane/routers/strategies.py`
- Create: `control-plane/tests/test_events_ws.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `WS /events` — clients receive JSON messages `{"type": "kill_all" | "promote" | "demote", "strategy_id": int?, "ts": iso8601}`.
  - `broadcast(event: dict) -> None` — awaitable, safe to call from routes.

- [ ] **Step 1: Write the failing test**

Create `control-plane/tests/test_events_ws.py`:
```python
import json
import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base, Strategy, StrategyStatus


@pytest.fixture()
def client(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite3")
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _s():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s", db_path=tmp_path / "t.sqlite3")
    with Session() as s:
        s.add(Strategy(id=1, name="t", code_path="p", status=StrategyStatus.live, capital_weight=0, max_notional=1, max_daily_loss=1, max_position=1))
        s.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_kill_all_broadcasts(client):
    with client.websocket_connect("/events") as ws:
        resp = client.post("/kill_all", headers={"Authorization": "Bearer s"})
        assert resp.status_code == 200
        msg = ws.receive_text()
        payload = json.loads(msg)
        assert payload["type"] == "kill_all"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd control-plane && uv run pytest tests/test_events_ws.py -v`

Expected: FAIL — no `/events` route.

- [ ] **Step 3: Implement the broadcaster**

Create `control-plane/src/control_plane/events.py`:
```python
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event)
        async with self._lock:
            stale: list[WebSocket] = []
            for client in self._clients:
                try:
                    await client.send_text(payload)
                except Exception:
                    stale.append(client)
            for s in stale:
                self._clients.discard(s)


broadcaster = Broadcaster()
```

Modify `control-plane/src/control_plane/main.py`:
```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from control_plane.events import broadcaster
from control_plane.routers import audit, health, kill, metrics, strategies

app = FastAPI(title="decipher-trader control-plane")
app.include_router(health.router)
app.include_router(strategies.router)
app.include_router(kill.router)
app.include_router(metrics.router)
app.include_router(audit.router)


@app.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    await broadcaster.register(ws)
    try:
        while True:
            await ws.receive_text()  # any client message keeps the socket alive
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)
```

Modify `control-plane/src/control_plane/routers/kill.py` — inside `kill_all`, after commit and before return, append:
```python
    from control_plane.events import broadcaster
    await broadcaster.broadcast({"type": "kill_all", "ts": now.isoformat(), "demoted": demoted})
```

Modify `control-plane/src/control_plane/routers/strategies.py` — at the end of `promote_strategy` (after `session.refresh(strategy)`), append:
```python
    from control_plane.events import broadcaster
    await broadcaster.broadcast({"type": "promote", "strategy_id": strategy.id, "ts": now.isoformat()})
```
Do the same for `demote_strategy`:
```python
    from control_plane.events import broadcaster
    await broadcaster.broadcast({"type": "demote", "strategy_id": strategy.id, "ts": datetime.now(timezone.utc).isoformat()})
```

- [ ] **Step 4: Run all control-plane tests to verify no regressions**

Run: `cd control-plane && uv run pytest -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add control-plane/src/control_plane/events.py \
        control-plane/src/control_plane/main.py \
        control-plane/src/control_plane/routers/kill.py \
        control-plane/src/control_plane/routers/strategies.py \
        control-plane/tests/test_events_ws.py
git commit -m "Broadcast promote, demote, and kill_all over the events websocket"
```

---

### Task 11: `control-plane` Dockerfile + compose entry

**Files:**
- Create: `control-plane/Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: `.env` values, `decipher-db` volume mount at `/app/data`.
- Produces: a running `control-plane` service on the `decipher` network at internal port `8000`.

- [ ] **Step 1: Create the Dockerfile**

Create `control-plane/Dockerfile`:
```dockerfile
FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.4.20
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN uv pip install --system -e "."

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY --from=build /app /app
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn control_plane.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 2: Add the service to `docker-compose.yml`**

Replace the `services: {}` line with:
```yaml
services:
  control-plane:
    build:
      context: ./control-plane
    networks:
      - decipher
    env_file:
      - .env
    volumes:
      - decipher-db:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status == 200 else 1)"]
      interval: 5s
      timeout: 3s
      retries: 6
```

- [ ] **Step 3: Verify the image builds and the container passes healthcheck**

```bash
cp .env.example .env
docker compose build control-plane
docker compose up -d control-plane
sleep 5
docker compose ps control-plane | grep -q "healthy" && echo OK
docker compose logs control-plane | tail -20
docker compose down
```

Expected: the `grep -q "healthy"` line prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add control-plane/Dockerfile docker-compose.yml
git commit -m "Containerize control-plane and add it to compose"
```

---

### Task 12: `nautilus-runner` bootstrap and control-plane HTTP client

**Files:**
- Create: `nautilus-runner/pyproject.toml`
- Create: `nautilus-runner/src/nautilus_runner/__init__.py`
- Create: `nautilus-runner/src/nautilus_runner/control_plane_client.py`
- Create: `nautilus-runner/tests/test_control_plane_client.py`

**Interfaces:**
- Consumes: env `CONTROL_PLANE_URL`, `OPERATOR_TOKEN`.
- Produces:
  - `ControlPlaneClient(base_url, token)` with:
    - `fetch_strategies(statuses: list[str]) -> list[dict]`
    - `post_audit(actor: str, action: str, payload: dict) -> None`
    - `post_metric(strategy_id: int, ts: datetime, pnl: float, sharpe: float, max_drawdown: float, n_trades: int) -> None`

- [ ] **Step 1: Create the package**

Create `nautilus-runner/pyproject.toml`:
```toml
[project]
name = "nautilus-runner"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "nautilus-trader==2.0.0rc6",
  "httpx==0.27.2",
  "websockets==13.1",
  "pydantic==2.9.2",
  "pydantic-settings==2.5.2",
]

[project.optional-dependencies]
dev = [
  "pytest==8.3.3",
  "pytest-asyncio==0.24.0",
  "respx==0.21.1",
  "ruff==0.6.9",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/nautilus_runner", "strategies"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Bootstrap:
```bash
cd nautilus-runner
uv venv
uv pip install -e ".[dev]"
```

Create `nautilus-runner/src/nautilus_runner/__init__.py` (empty).

- [ ] **Step 2: Write the failing test**

Create `nautilus-runner/tests/test_control_plane_client.py`:
```python
from datetime import datetime, timezone

import httpx
import pytest
import respx

from nautilus_runner.control_plane_client import ControlPlaneClient


BASE = "http://cp.test"


@pytest.mark.asyncio
async def test_fetch_strategies_sends_status_csv():
    async with respx.mock(base_url=BASE, assert_all_called=True) as router:
        route = router.get("/strategies").mock(return_value=httpx.Response(200, json=[{"id": 1}]))
        client = ControlPlaneClient(BASE, "tok")
        rows = await client.fetch_strategies(["paper", "live"])
        assert rows == [{"id": 1}]
        assert route.calls[0].request.url.params["status"] == "paper,live"


@pytest.mark.asyncio
async def test_post_audit_sends_bearer():
    async with respx.mock(base_url=BASE) as router:
        router.post("/audit").mock(return_value=httpx.Response(201, json={"id": 1, "ts": "2026-01-01T00:00:00Z", "actor": "r", "action": "a", "payload_json": "{}"}))
        client = ControlPlaneClient(BASE, "tok")
        await client.post_audit("runner", "start", {"strategy_id": 1})
        req = router.calls[0].request
        assert req.headers["authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_post_metric_serializes_ts_iso():
    async with respx.mock(base_url=BASE) as router:
        router.post("/metrics/1").mock(return_value=httpx.Response(201, json={"strategy_id": 1, "ts": "2026-01-01T00:00:00Z", "pnl": 0, "sharpe": 0, "max_drawdown": 0, "n_trades": 0}))
        client = ControlPlaneClient(BASE, "tok")
        await client.post_metric(1, datetime(2026, 1, 1, tzinfo=timezone.utc), 0.0, 0.0, 0.0, 0)
        body = router.calls[0].request.content.decode()
        assert "2026-01-01T00:00:00+00:00" in body
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd nautilus-runner && uv run pytest tests/test_control_plane_client.py -v`

Expected: FAIL — module not found.

- [ ] **Step 4: Implement the client**

Create `nautilus-runner/src/nautilus_runner/control_plane_client.py`:
```python
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class ControlPlaneClient:
    def __init__(self, base_url: str, token: str, timeout_secs: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout_secs

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, headers=self._headers, timeout=self._timeout)

    async def fetch_strategies(self, statuses: list[str]) -> list[dict[str, Any]]:
        async with await self._client() as c:
            resp = await c.get("/strategies", params={"status": ",".join(statuses)} if statuses else None)
            resp.raise_for_status()
            return resp.json()

    async def post_audit(self, actor: str, action: str, payload: dict[str, Any]) -> None:
        import json as _json

        async with await self._client() as c:
            resp = await c.post("/audit", json={"actor": actor, "action": action, "payload_json": _json.dumps(payload, default=str)})
            resp.raise_for_status()

    async def post_metric(
        self,
        strategy_id: int,
        ts: datetime,
        pnl: float,
        sharpe: float,
        max_drawdown: float,
        n_trades: int,
    ) -> None:
        async with await self._client() as c:
            resp = await c.post(
                f"/metrics/{strategy_id}",
                json={
                    "ts": ts.isoformat(),
                    "pnl": pnl,
                    "sharpe": sharpe,
                    "max_drawdown": max_drawdown,
                    "n_trades": n_trades,
                },
            )
            resp.raise_for_status()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd nautilus-runner && uv run pytest tests/test_control_plane_client.py -v`

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add nautilus-runner/pyproject.toml \
        nautilus-runner/src/nautilus_runner/__init__.py \
        nautilus-runner/src/nautilus_runner/control_plane_client.py \
        nautilus-runner/tests/test_control_plane_client.py
git commit -m "Bootstrap nautilus-runner and add control-plane HTTP client"
```

---

### Task 13: `nautilus-runner` config builder with G1 + G2 + G3

**Files:**
- Create: `nautilus-runner/src/nautilus_runner/config.py`
- Create: `nautilus-runner/tests/test_config.py`

**Interfaces:**
- Consumes: env `TRADING_MODE`, `NAUTILUS_TRADER_ID`, `HYPERLIQUID_TESTNET_PRIVATE_KEY`, `HYPERLIQUID_TESTNET_ACCOUNT_ID`, `HYPERLIQUID_MAINNET_PRIVATE_KEY`, `HYPERLIQUID_MAINNET_ACCOUNT_ID`, `HEARTBEAT_INTERVAL_SECS`, `HEARTBEAT_MISS_LIMIT`. Rows fetched from control-plane.
- Produces:
  - `RunnerSettings` — env-derived settings.
  - `assert_live_startup_safe(strategies_from_registry, trading_mode)` — raises `RuntimeError` if `TRADING_MODE=live` and any `status=live` row lacks `promoted_at`/`promoted_by` (G1).
  - `hyperliquid_env_for(trading_mode)` — returns `HyperliquidEnvironment.TESTNET` or `MAINNET` (G2).
  - `build_risk_caps(strategy_row) -> dict[str, float]` — dict fed to Nautilus risk engine and per-strategy check (G3).

- [ ] **Step 1: Write the failing test**

Create `nautilus-runner/tests/test_config.py`:
```python
import pytest

from nautilus_runner.config import (
    RunnerSettings,
    assert_live_startup_safe,
    build_risk_caps,
    hyperliquid_env_for,
)


def test_env_paper_maps_to_testnet():
    env = hyperliquid_env_for("paper")
    assert env.name == "TESTNET"


def test_env_live_maps_to_mainnet():
    env = hyperliquid_env_for("live")
    assert env.name == "MAINNET"


def test_env_invalid_mode_raises():
    with pytest.raises(ValueError):
        hyperliquid_env_for("prod")


def test_g1_rejects_live_row_missing_promotion_evidence():
    rows = [{"id": 1, "status": "live", "promoted_at": None, "promoted_by": None}]
    with pytest.raises(RuntimeError, match="never promoted"):
        assert_live_startup_safe(rows, "live")


def test_g1_allows_promoted_live_rows():
    rows = [{"id": 1, "status": "live", "promoted_at": "2026-01-01T00:00:00Z", "promoted_by": "operator"}]
    assert_live_startup_safe(rows, "live") is None


def test_g1_no_check_in_paper_mode():
    rows = [{"id": 1, "status": "live", "promoted_at": None, "promoted_by": None}]
    assert_live_startup_safe(rows, "paper") is None


def test_build_risk_caps_shape():
    row = {"id": 5, "max_notional": 100.0, "max_daily_loss": 10.0, "max_position": 0.5}
    caps = build_risk_caps(row)
    assert caps == {"strategy_id": 5, "max_notional": 100.0, "max_daily_loss": 10.0, "max_position": 0.5}


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://cp:8000")
    monkeypatch.setenv("OPERATOR_TOKEN", "t")
    s = RunnerSettings()
    assert s.trading_mode == "paper"
    assert s.trader_id == "DECIPHER-001"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd nautilus-runner && uv run pytest tests/test_config.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the config module**

Create `nautilus-runner/src/nautilus_runner/config.py`:
```python
from __future__ import annotations

from typing import Any, Literal

from nautilus_trader.adapters.hyperliquid import HyperliquidEnvironment
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    trading_mode: Literal["paper", "live"] = Field(default="paper", alias="TRADING_MODE")
    trader_id: str = Field(default="DECIPHER-001", alias="NAUTILUS_TRADER_ID")
    control_plane_url: str = Field(alias="CONTROL_PLANE_URL")
    operator_token: str = Field(alias="OPERATOR_TOKEN")
    heartbeat_interval_secs: int = Field(default=30, alias="HEARTBEAT_INTERVAL_SECS")
    heartbeat_miss_limit: int = Field(default=2, alias="HEARTBEAT_MISS_LIMIT")
    testnet_private_key: str | None = Field(default=None, alias="HYPERLIQUID_TESTNET_PRIVATE_KEY")
    testnet_account_id: str = Field(default="HYPERLIQUID-TESTNET-001", alias="HYPERLIQUID_TESTNET_ACCOUNT_ID")
    mainnet_private_key: str | None = Field(default=None, alias="HYPERLIQUID_MAINNET_PRIVATE_KEY")
    mainnet_account_id: str | None = Field(default=None, alias="HYPERLIQUID_MAINNET_ACCOUNT_ID")


def hyperliquid_env_for(trading_mode: str) -> HyperliquidEnvironment:
    if trading_mode == "paper":
        return HyperliquidEnvironment.TESTNET
    if trading_mode == "live":
        return HyperliquidEnvironment.MAINNET
    raise ValueError(f"unknown trading_mode {trading_mode!r}; expected 'paper' or 'live'")


def assert_live_startup_safe(strategies: list[dict[str, Any]], trading_mode: str) -> None:
    if trading_mode != "live":
        return
    for row in strategies:
        if row["status"] != "live":
            continue
        if not row.get("promoted_at") or not row.get("promoted_by"):
            raise RuntimeError(
                f"refuse to start in live mode: strategy id={row['id']} is live but never promoted"
            )


def build_risk_caps(row: dict[str, Any]) -> dict[str, float]:
    return {
        "strategy_id": row["id"],
        "max_notional": float(row["max_notional"]),
        "max_daily_loss": float(row["max_daily_loss"]),
        "max_position": float(row["max_position"]),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd nautilus-runner && uv run pytest tests/test_config.py -v`

Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add nautilus-runner/src/nautilus_runner/config.py nautilus-runner/tests/test_config.py
git commit -m "Enforce mode-scoped credentials and live-mode promotion invariant"
```

---

### Task 14: Toy momentum `Strategy` subclass + backtest unit test

**Files:**
- Create: `nautilus-runner/strategies/toy_momentum/__init__.py`
- Create: `nautilus-runner/strategies/toy_momentum/strategy.py`
- Create: `nautilus-runner/tests/test_toy_momentum.py`

**Interfaces:**
- Consumes: `nautilus_trader.trading.Strategy`, `nautilus_trader.config.StrategyConfig`.
- Produces:
  - `strategies.toy_momentum.strategy.ToyMomentumConfig(instrument_id: InstrumentId, bar_type: BarType, trade_size: Decimal, max_notional: float, max_daily_loss: float, max_position: float, fast_period: int = 5, slow_period: int = 20)`
  - `strategies.toy_momentum.strategy.ToyMomentum(config: ToyMomentumConfig)` — buys when fast MA crosses above slow MA, sells on the reverse crossing. Enforces G3 at the strategy layer: refuses to submit any order whose signed position after execution would exceed `max_position`. (Phase 1 chooses in-strategy enforcement because the Nautilus `RiskEngine` API for per-strategy notional caps requires further investigation; global caps could also be plumbed later without invalidating this per-strategy check.)

- [ ] **Step 1: Write the failing test**

Create `nautilus-runner/tests/test_toy_momentum.py`:
```python
from decimal import Decimal

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model import BarType, InstrumentId, Venue

from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig


@pytest.mark.skip(reason="Wired up during implementation once Nautilus backtest fixtures for HYPERLIQUID testnet are added")
def test_toy_momentum_backtest_no_crash():
    # Placeholder — the actual backtest wiring is added in Step 3 below.
    pass


def test_config_defaults_are_sane():
    instrument = InstrumentId.from_str("BTC-USD.HYPERLIQUID")
    bar_type = BarType.from_str("BTC-USD.HYPERLIQUID-1-MINUTE-MID-INTERNAL")
    config = ToyMomentumConfig(
        instrument_id=instrument,
        bar_type=bar_type,
        trade_size=Decimal("0.001"),
        max_notional=1000.0,
        max_daily_loss=100.0,
        max_position=1.0,
    )
    assert config.fast_period == 5
    assert config.slow_period == 20
    assert config.slow_period > config.fast_period


def test_would_breach_position_cap_returns_true_when_over():
    from strategies.toy_momentum.strategy import ToyMomentumConfig, would_breach_position_cap

    assert would_breach_position_cap(current_position=0.9, delta=0.2, cap=1.0) is True
    assert would_breach_position_cap(current_position=0.5, delta=0.2, cap=1.0) is False
    # symmetric on the short side
    assert would_breach_position_cap(current_position=-0.9, delta=-0.2, cap=1.0) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd nautilus-runner && uv run pytest tests/test_toy_momentum.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the strategy**

Create `nautilus-runner/strategies/toy_momentum/__init__.py` (empty).

Create `nautilus-runner/strategies/toy_momentum/strategy.py`:
```python
from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Quantity
from nautilus_trader.trading import Strategy


def would_breach_position_cap(current_position: float, delta: float, cap: float) -> bool:
    """Return True if applying `delta` to `current_position` would push |pos| beyond `cap`."""
    projected = current_position + delta
    return abs(projected) > cap


class ToyMomentumConfig(StrategyConfig):
    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        bar_type: BarType,
        trade_size: Decimal,
        max_notional: float,
        max_daily_loss: float,
        max_position: float,
        fast_period: int = 5,
        slow_period: int = 20,
        **_kwargs: Any,
    ) -> None:
        super().__init__()
        assert slow_period > fast_period, "slow_period must exceed fast_period"
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.trade_size = trade_size
        self.max_notional = max_notional
        self.max_daily_loss = max_daily_loss
        self.max_position = max_position
        self.fast_period = fast_period
        self.slow_period = slow_period


class ToyMomentum(Strategy):
    def __init__(self, config: ToyMomentumConfig) -> None:
        super().__init__(config)
        self._config = config
        self._fast: deque[float] = deque(maxlen=config.fast_period)
        self._slow: deque[float] = deque(maxlen=config.slow_period)
        self._signed_position: float = 0.0

    def on_start(self) -> None:
        self.subscribe_bars(self._config.bar_type)

    def _submit_capped(self, side: OrderSide, delta: float) -> None:
        # G3: refuse to breach the per-strategy position cap.
        if would_breach_position_cap(self._signed_position, delta, self._config.max_position):
            self.log.warning(f"skip order: would breach max_position={self._config.max_position}")
            return
        self.submit_order(
            self.order_factory.market(
                instrument_id=self._config.instrument_id,
                order_side=side,
                quantity=Quantity.from_str(str(self._config.trade_size)),
            )
        )
        self._signed_position += delta

    def on_bar(self, bar: Bar) -> None:
        price = float(bar.close)
        self._fast.append(price)
        self._slow.append(price)
        if len(self._slow) < self._config.slow_period:
            return

        fast_ma = sum(self._fast) / len(self._fast)
        slow_ma = sum(self._slow) / len(self._slow)

        want_long = fast_ma > slow_ma
        trade_qty = float(self._config.trade_size)
        if want_long and self._signed_position <= 0:
            self._submit_capped(OrderSide.BUY, +trade_qty)
        elif not want_long and self._signed_position >= 0:
            self._submit_capped(OrderSide.SELL, -trade_qty)

    def on_stop(self) -> None:
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd nautilus-runner && uv run pytest tests/test_toy_momentum.py -v`

Expected: `test_config_defaults_are_sane` and `test_would_breach_position_cap_returns_true_when_over` PASS; the backtest test remains skipped (see note below).

- [ ] **Step 5: Note limitation and open a follow-up TODO for the maintainer**

The strategy is unit-testable through its config today. A full backtest test requires a Hyperliquid-specific historical data fixture that ships with the pinned `nautilus-trader` version. The skipped test placeholder is deliberate — enabling it belongs to Task 24's E2E work, where a mocked Hyperliquid feed is stood up.

- [ ] **Step 6: Commit**

```bash
git add nautilus-runner/strategies/toy_momentum/__init__.py \
        nautilus-runner/strategies/toy_momentum/strategy.py \
        nautilus-runner/tests/test_toy_momentum.py
git commit -m "Add toy momentum strategy with config invariants"
```

---

### Task 15: `nautilus-runner` LiveNode entrypoint

**Files:**
- Create: `nautilus-runner/src/nautilus_runner/main.py`

**Interfaces:**
- Consumes: `RunnerSettings`, `ControlPlaneClient`, `assert_live_startup_safe`, `hyperliquid_env_for`, `build_risk_caps`, `ToyMomentum`, `ToyMomentumConfig`.
- Produces: `main()` — CLI entrypoint that boots a `LiveNode` with the strategies fetched from control-plane and blocks on `node.run()`.

- [ ] **Step 1: Implement the entrypoint**

Create `nautilus-runner/src/nautilus_runner/main.py`:
```python
from __future__ import annotations

import asyncio
from decimal import Decimal

from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidDataClientFactory,
    HyperliquidExecutionClientConfig,
    HyperliquidExecutionClientFactory,
)
from nautilus_trader.common import Environment
from nautilus_trader.config import LiveRiskEngineConfig
from nautilus_trader.live import LiveNode
from nautilus_trader.model import AccountId, BarType, ClientId, InstrumentId, StrategyId, TraderId

from nautilus_runner.config import (
    RunnerSettings,
    assert_live_startup_safe,
    build_risk_caps,
    hyperliquid_env_for,
)
from nautilus_runner.control_plane_client import ControlPlaneClient
from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig

HYPERLIQUID = "HYPERLIQUID"


async def _fetch_strategies(settings: RunnerSettings) -> list[dict]:
    client = ControlPlaneClient(settings.control_plane_url, settings.operator_token)
    wanted = ["paper"] if settings.trading_mode == "paper" else ["live"]
    return await client.fetch_strategies(wanted)


def _build_node(settings: RunnerSettings, strategy_rows: list[dict]) -> LiveNode:
    env = hyperliquid_env_for(settings.trading_mode)
    account_id_str = settings.testnet_account_id if settings.trading_mode == "paper" else settings.mainnet_account_id
    private_key = settings.testnet_private_key if settings.trading_mode == "paper" else settings.mainnet_private_key
    if not account_id_str or not private_key:
        raise RuntimeError(f"missing Hyperliquid credentials for mode={settings.trading_mode}")

    node = (
        LiveNode.builder(f"DECIPHER-{settings.trading_mode.upper()}", TraderId.from_str(settings.trader_id), Environment.LIVE)
        .with_reconciliation(reconciliation=True)
        .with_risk_engine_config(LiveRiskEngineConfig(bypass=False))
        .add_data_client(
            None,
            HyperliquidDataClientFactory(),
            HyperliquidDataClientConfig(environment=env, private_key=private_key),
        )
        .add_exec_client(
            None,
            HyperliquidExecutionClientFactory(),
            HyperliquidExecutionClientConfig(
                account_id=AccountId.from_str(account_id_str),
                environment=env,
                private_key=private_key,
            ),
        )
        .build()
    )

    for row in strategy_rows:
        caps = build_risk_caps(row)
        instrument = InstrumentId.from_str(f"BTC-USD-PERP.{HYPERLIQUID}")
        bar_type = BarType.from_str(f"{instrument}-1-MINUTE-MID-INTERNAL")
        cfg = ToyMomentumConfig(
            instrument_id=instrument,
            bar_type=bar_type,
            trade_size=Decimal("0.001"),
            max_notional=caps["max_notional"],
            max_daily_loss=caps["max_daily_loss"],
            max_position=caps["max_position"],
            strategy_id=StrategyId.from_str(f"DECIPHER-{row['id']:04d}"),
        )
        node.add_strategy(ToyMomentum(cfg))

    return node


def main() -> None:
    settings = RunnerSettings()
    rows = asyncio.run(_fetch_strategies(settings))
    assert_live_startup_safe(rows, settings.trading_mode)
    node = _build_node(settings, rows)
    node.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual smoke check (deferred to Task 24 E2E)**

Full runtime integration is exercised in Task 24 with a mocked Hyperliquid. This task's exit criterion is that `python -c "from nautilus_runner.main import _build_node"` imports cleanly.

Run:
```bash
cd nautilus-runner
uv run python -c "from nautilus_runner.main import _build_node, _fetch_strategies, main; print('import ok')"
```

Expected output: `import ok`.

- [ ] **Step 3: Commit**

```bash
git add nautilus-runner/src/nautilus_runner/main.py
git commit -m "Boot LiveNode with strategies fetched from control-plane"
```

---

### Task 16: `nautilus-runner` heartbeat (G9) + kill listener

**Files:**
- Create: `nautilus-runner/src/nautilus_runner/heartbeat.py`
- Create: `nautilus-runner/src/nautilus_runner/kill_listener.py`
- Modify: `nautilus-runner/src/nautilus_runner/main.py`
- Create: `nautilus-runner/tests/test_heartbeat.py`

**Interfaces:**
- Consumes: `ControlPlaneClient`, `RunnerSettings`.
- Produces:
  - `heartbeat_loop(client, interval_secs, miss_limit, stop_event, on_miss_callback)` — coroutine writing an audit `heartbeat` entry every interval, aborting via `on_miss_callback` after `miss_limit` consecutive failures.
  - `kill_listener_loop(control_plane_url, token, on_kill)` — coroutine listening on `WS /events` and invoking `on_kill()` on any `{"type":"kill_all"}` message.

- [ ] **Step 1: Write the failing test**

Create `nautilus-runner/tests/test_heartbeat.py`:
```python
import asyncio

import httpx
import pytest
import respx

from nautilus_runner.control_plane_client import ControlPlaneClient
from nautilus_runner.heartbeat import heartbeat_loop


@pytest.mark.asyncio
async def test_heartbeat_stops_after_miss_limit():
    misses = 0

    def _cb() -> None:
        nonlocal misses
        misses += 1

    with respx.mock(base_url="http://cp.test", assert_all_called=False) as router:
        router.post("/audit").mock(return_value=httpx.Response(500))
        client = ControlPlaneClient("http://cp.test", "tok", timeout_secs=0.1)
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat_loop(client, interval_secs=0.05, miss_limit=2, stop_event=stop, on_miss=_cb))
        await asyncio.sleep(0.25)
        stop.set()
        await task
        assert misses >= 1


@pytest.mark.asyncio
async def test_heartbeat_resets_after_success():
    responses = [httpx.Response(500), httpx.Response(201, json={"id": 1, "ts": "2026-01-01T00:00:00Z", "actor": "r", "action": "heartbeat", "payload_json": "{}"})]

    with respx.mock(base_url="http://cp.test") as router:
        route = router.post("/audit")
        route.side_effect = responses
        misses = 0

        def _cb() -> None:
            nonlocal misses
            misses += 1

        client = ControlPlaneClient("http://cp.test", "tok", timeout_secs=0.1)
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat_loop(client, interval_secs=0.05, miss_limit=3, stop_event=stop, on_miss=_cb))
        await asyncio.sleep(0.2)
        stop.set()
        await task
        assert misses == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd nautilus-runner && uv run pytest tests/test_heartbeat.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement heartbeat and kill listener**

Create `nautilus-runner/src/nautilus_runner/heartbeat.py`:
```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from nautilus_runner.control_plane_client import ControlPlaneClient


async def heartbeat_loop(
    client: ControlPlaneClient,
    interval_secs: float,
    miss_limit: int,
    stop_event: asyncio.Event,
    on_miss: Callable[[], None],
) -> None:
    consecutive_misses = 0
    while not stop_event.is_set():
        try:
            await client.post_audit("runner", "heartbeat", {"ts": datetime.now(timezone.utc).isoformat()})
            consecutive_misses = 0
        except Exception:
            consecutive_misses += 1
            if consecutive_misses >= miss_limit:
                on_miss()
                consecutive_misses = 0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_secs)
        except asyncio.TimeoutError:
            continue
```

Create `nautilus-runner/src/nautilus_runner/kill_listener.py`:
```python
from __future__ import annotations

import asyncio
import json
from typing import Callable

import websockets


async def kill_listener_loop(
    control_plane_ws_url: str,
    on_kill: Callable[[], None],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            async with websockets.connect(control_plane_ws_url) as ws:
                while not stop_event.is_set():
                    msg = await ws.recv()
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue
                    if data.get("type") == "kill_all":
                        on_kill()
        except Exception:
            await asyncio.sleep(1.0)
```

Modify `nautilus-runner/src/nautilus_runner/main.py` — replace the `main()` function with:
```python
def main() -> None:
    import signal
    import threading

    settings = RunnerSettings()
    rows = asyncio.run(_fetch_strategies(settings))
    assert_live_startup_safe(rows, settings.trading_mode)
    node = _build_node(settings, rows)

    stop_event = asyncio.Event()
    ws_url = settings.control_plane_url.replace("http://", "ws://").replace("https://", "wss://") + "/events"

    def _trigger_kill() -> None:
        try:
            node.stop()
        except Exception:
            pass

    loop = asyncio.new_event_loop()

    def _background() -> None:
        asyncio.set_event_loop(loop)
        client = ControlPlaneClient(settings.control_plane_url, settings.operator_token)
        loop.run_until_complete(
            asyncio.gather(
                heartbeat_loop(client, settings.heartbeat_interval_secs, settings.heartbeat_miss_limit, stop_event, _trigger_kill),
                kill_listener_loop(ws_url, _trigger_kill, stop_event),
            )
        )

    t = threading.Thread(target=_background, daemon=True)
    t.start()

    def _handle_sig(_signum, _frame) -> None:
        loop.call_soon_threadsafe(stop_event.set)
        node.stop()

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    node.run()
    loop.call_soon_threadsafe(stop_event.set)
```

Add the imports at the top of `main.py`:
```python
from nautilus_runner.heartbeat import heartbeat_loop
from nautilus_runner.kill_listener import kill_listener_loop
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd nautilus-runner && uv run pytest tests/test_heartbeat.py -v`

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add nautilus-runner/src/nautilus_runner/heartbeat.py \
        nautilus-runner/src/nautilus_runner/kill_listener.py \
        nautilus-runner/src/nautilus_runner/main.py \
        nautilus-runner/tests/test_heartbeat.py
git commit -m "Add heartbeat with miss-limit auto-kill and kill-all websocket listener"
```

---

### Task 17: `nautilus-runner` Dockerfile + compose entry

**Files:**
- Create: `nautilus-runner/Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: env `TRADING_MODE`, `CONTROL_PLANE_URL`, `OPERATOR_TOKEN`, `HYPERLIQUID_*` — mounted per mode via override files (Task 23).
- Produces: `nautilus-runner` service depending on healthy `control-plane`.

- [ ] **Step 1: Create the Dockerfile**

Create `nautilus-runner/Dockerfile`:
```dockerfile
FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.4.20
COPY pyproject.toml ./
COPY src ./src
COPY strategies ./strategies
RUN uv pip install --system -e "."

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY --from=build /app /app
CMD ["python", "-m", "nautilus_runner.main"]
```

- [ ] **Step 2: Add to `docker-compose.yml`**

Append under `services:`:
```yaml
  nautilus-runner:
    build:
      context: ./nautilus-runner
    networks:
      - decipher
    env_file:
      - .env
    depends_on:
      control-plane:
        condition: service_healthy
    restart: unless-stopped
```

- [ ] **Step 3: Verify build succeeds**

```bash
docker compose build nautilus-runner
```

Expected: image builds. (Runtime is exercised in Task 24.)

- [ ] **Step 4: Commit**

```bash
git add nautilus-runner/Dockerfile docker-compose.yml
git commit -m "Containerize nautilus-runner and wire it to control-plane"
```

---

### Task 18: `dashboard` Next.js scaffold + login + session

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/next.config.js`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/src/app/layout.tsx`
- Create: `dashboard/src/app/page.tsx`  (placeholder)
- Create: `dashboard/src/app/login/page.tsx`
- Create: `dashboard/src/lib/session.ts`
- Create: `dashboard/src/app/api/auth/route.ts`
- Create: `dashboard/tests/session.test.ts`

**Interfaces:**
- Consumes: env `DASHBOARD_SESSION_SECRET`, `DASHBOARD_OPERATOR_USERNAME`, `DASHBOARD_OPERATOR_PASSWORD_BCRYPT`.
- Produces:
  - `session.ts` exports `getSession(request, response)`, `hashPassword(plain)`, `verifyPassword(plain, hash)` (bcrypt via `bcryptjs`).
  - `/login` page.
  - `POST /api/auth` — sets session cookie on success.
  - `DELETE /api/auth` — clears session.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "dashboard",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev -p 3000",
    "build": "next build",
    "start": "next start -p 3000",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "15.0.2",
    "react": "19.0.0",
    "react-dom": "19.0.0",
    "iron-session": "8.0.3",
    "bcryptjs": "2.4.3"
  },
  "devDependencies": {
    "typescript": "5.6.3",
    "@types/node": "22.7.5",
    "@types/react": "19.0.0",
    "@types/react-dom": "19.0.0",
    "@types/bcryptjs": "2.4.6",
    "vitest": "2.1.3",
    "@vitest/coverage-v8": "2.1.3"
  }
}
```

- [ ] **Step 2: Create `next.config.js` and `tsconfig.json`**

`dashboard/next.config.js`:
```javascript
/** @type {import('next').NextConfig} */
module.exports = { reactStrictMode: true, output: "standalone" };
```

`dashboard/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src/**/*.ts", "src/**/*.tsx", "next-env.d.ts", ".next/types/**/*.ts", "tests/**/*.ts"],
  "exclude": ["node_modules"]
}
```

Install:
```bash
cd dashboard
npm install
```

- [ ] **Step 3: Write the failing test**

Create `dashboard/tests/session.test.ts`:
```typescript
import { describe, expect, it } from "vitest";
import { hashPassword, verifyPassword } from "../src/lib/session";

describe("session helpers", () => {
  it("hash then verify succeeds", async () => {
    const hash = await hashPassword("hunter2");
    expect(await verifyPassword("hunter2", hash)).toBe(true);
  });

  it("wrong password fails", async () => {
    const hash = await hashPassword("hunter2");
    expect(await verifyPassword("wrong", hash)).toBe(false);
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd dashboard && npm test`

Expected: FAIL — module not found.

- [ ] **Step 5: Implement session helpers, login page, and API route**

Create `dashboard/src/lib/session.ts`:
```typescript
import { getIronSession, IronSession } from "iron-session";
import bcrypt from "bcryptjs";
import { cookies } from "next/headers";

export interface SessionData {
  operator?: string;
}

const cookieName = "decipher_session";

export async function getSession(): Promise<IronSession<SessionData>> {
  const password = process.env.DASHBOARD_SESSION_SECRET;
  if (!password || password.length < 32) {
    throw new Error("DASHBOARD_SESSION_SECRET must be at least 32 chars");
  }
  return getIronSession<SessionData>(cookies(), { password, cookieName });
}

export async function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, 10);
}

export async function verifyPassword(plain: string, hash: string): Promise<boolean> {
  return bcrypt.compare(plain, hash);
}
```

Create `dashboard/src/app/layout.tsx`:
```tsx
export const metadata = { title: "decipher-trader" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>{children}</body>
    </html>
  );
}
```

Create `dashboard/src/app/page.tsx`:
```tsx
import { getSession } from "@/lib/session";
import { redirect } from "next/navigation";

export default async function Home() {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  return <main style={{ padding: 24 }}><h1>decipher-trader</h1><p>Strategies list — Task 20.</p></main>;
}
```

Create `dashboard/src/app/login/page.tsx`:
```tsx
"use client";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const router = useRouter();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const resp = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (resp.ok) router.push("/");
    else setErr("Invalid credentials.");
  }

  return (
    <main style={{ maxWidth: 320, margin: "80px auto", padding: 24 }}>
      <h1>Sign in</h1>
      <form onSubmit={onSubmit}>
        <label>Username<br /><input value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <br /><br />
        <label>Password<br /><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <br /><br />
        <button type="submit">Sign in</button>
        {err ? <p style={{ color: "red" }}>{err}</p> : null}
      </form>
    </main>
  );
}
```

Create `dashboard/src/app/api/auth/route.ts`:
```typescript
import { NextRequest, NextResponse } from "next/server";
import { getSession, verifyPassword } from "@/lib/session";

export async function POST(req: NextRequest) {
  const { username, password } = await req.json();
  const expectedUser = process.env.DASHBOARD_OPERATOR_USERNAME;
  const hash = process.env.DASHBOARD_OPERATOR_PASSWORD_BCRYPT;
  if (!expectedUser || !hash) return NextResponse.json({ ok: false }, { status: 500 });
  if (username !== expectedUser) return NextResponse.json({ ok: false }, { status: 401 });
  if (!(await verifyPassword(password, hash))) return NextResponse.json({ ok: false }, { status: 401 });

  const session = await getSession();
  session.operator = expectedUser;
  await session.save();
  return NextResponse.json({ ok: true });
}

export async function DELETE() {
  const session = await getSession();
  session.destroy();
  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd dashboard && npm test`

Expected: 2 PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard/package.json dashboard/next.config.js dashboard/tsconfig.json \
        dashboard/src/app/layout.tsx dashboard/src/app/page.tsx dashboard/src/app/login/page.tsx \
        dashboard/src/lib/session.ts dashboard/src/app/api/auth/route.ts \
        dashboard/tests/session.test.ts
git commit -m "Scaffold Next.js dashboard with operator login and session"
```

---

### Task 19: `dashboard` server-side control-plane client + `/api/control/*` proxy

**Files:**
- Create: `dashboard/src/lib/controlPlane.ts`
- Create: `dashboard/src/app/api/control/[...path]/route.ts`
- Create: `dashboard/tests/controlPlane.test.ts`

**Interfaces:**
- Consumes: env `CONTROL_PLANE_URL`, `OPERATOR_TOKEN`, session.
- Produces:
  - `controlPlane.get(path: string): Promise<Response>` and `.post(path, body)`, `.delete(path)`.
  - `/api/control/*` — passes through to control-plane, attaching `OPERATOR_TOKEN` server-side, rejecting when session absent.

- [ ] **Step 1: Write the failing test**

Create `dashboard/tests/controlPlane.test.ts`:
```typescript
import { describe, expect, it, vi } from "vitest";
import { controlPlane } from "../src/lib/controlPlane";

describe("controlPlane wrapper", () => {
  it("attaches bearer token", async () => {
    process.env.CONTROL_PLANE_URL = "http://cp.test";
    process.env.OPERATOR_TOKEN = "tok";
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("ok", { status: 200 }));
    await controlPlane.get("/strategies");
    const call = fetchSpy.mock.calls[0];
    expect(call[0]).toBe("http://cp.test/strategies");
    const headers = call[1]!.headers as Record<string, string>;
    expect(headers["authorization"]).toBe("Bearer tok");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd dashboard && npm test -- controlPlane.test.ts`

Expected: FAIL.

- [ ] **Step 3: Implement**

Create `dashboard/src/lib/controlPlane.ts`:
```typescript
function url(path: string): string {
  const base = process.env.CONTROL_PLANE_URL;
  if (!base) throw new Error("CONTROL_PLANE_URL not set");
  return `${base.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

function headers(): Record<string, string> {
  const token = process.env.OPERATOR_TOKEN;
  if (!token) throw new Error("OPERATOR_TOKEN not set");
  return { authorization: `Bearer ${token}`, "content-type": "application/json" };
}

export const controlPlane = {
  get: (path: string) => fetch(url(path), { headers: headers() }),
  post: (path: string, body: unknown) => fetch(url(path), { method: "POST", headers: headers(), body: JSON.stringify(body ?? {}) }),
  delete: (path: string) => fetch(url(path), { method: "DELETE", headers: headers() }),
};
```

Create `dashboard/src/app/api/control/[...path]/route.ts`:
```typescript
import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";

async function ensureSession() {
  const s = await getSession();
  if (!s.operator) return NextResponse.json({ ok: false }, { status: 401 });
  return null;
}

async function forward(req: NextRequest, params: { path: string[] }, method: "GET" | "POST" | "DELETE") {
  const auth = await ensureSession();
  if (auth) return auth;
  const path = "/" + params.path.join("/") + (req.nextUrl.search ?? "");
  const body = method === "GET" ? undefined : await req.text();
  const resp =
    method === "GET"
      ? await controlPlane.get(path)
      : method === "POST"
        ? await controlPlane.post(path, body ? JSON.parse(body) : undefined)
        : await controlPlane.delete(path);
  const text = await resp.text();
  return new NextResponse(text, { status: resp.status, headers: { "content-type": resp.headers.get("content-type") ?? "application/json" } });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, await ctx.params, "GET");
}
export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, await ctx.params, "POST");
}
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, await ctx.params, "DELETE");
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd dashboard && npm test -- controlPlane.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/controlPlane.ts \
        dashboard/src/app/api/control/[...path]/route.ts \
        dashboard/tests/controlPlane.test.ts
git commit -m "Proxy dashboard requests to control-plane with server-side operator token"
```

---

### Task 20: `dashboard` strategy list + detail + promote/demote buttons

**Files:**
- Modify: `dashboard/src/app/page.tsx`
- Create: `dashboard/src/app/strategy/[id]/page.tsx`
- Create: `dashboard/src/components/StatusBadge.tsx`
- Create: `dashboard/src/components/PnLSparkline.tsx`

**Interfaces:**
- Consumes: `/api/control/strategies`, `/api/control/strategies/{id}/promote`, `/api/control/strategies/{id}/demote`.
- Produces: rendered pages; two client-side buttons that call the proxy and refresh.

- [ ] **Step 1: Implement `StatusBadge` and `PnLSparkline`**

`dashboard/src/components/StatusBadge.tsx`:
```tsx
const COLORS: Record<string, string> = {
  draft: "#888", backtest: "#3b82f6", paper: "#f59e0b", live: "#10b981", retired: "#4b5563",
};

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span style={{ backgroundColor: COLORS[status] ?? "#000", color: "white", padding: "2px 8px", borderRadius: 6, fontSize: 12 }}>
      {status}
    </span>
  );
}
```

`dashboard/src/components/PnLSparkline.tsx`:
```tsx
export default function PnLSparkline({ series }: { series: number[] }) {
  if (series.length === 0) return <span style={{ color: "#999" }}>no data</span>;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const w = 100;
  const h = 24;
  const step = w / Math.max(series.length - 1, 1);
  const points = series.map((v, i) => `${i * step},${h - ((v - min) / range) * h}`).join(" ");
  return (
    <svg width={w} height={h}>
      <polyline points={points} fill="none" stroke="#111" strokeWidth={1.5} />
    </svg>
  );
}
```

- [ ] **Step 2: Replace `dashboard/src/app/page.tsx`**

```tsx
import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";
import { redirect } from "next/navigation";
import StatusBadge from "@/components/StatusBadge";
import Link from "next/link";

interface Row { id: number; name: string; status: string; capital_weight: number }

export default async function Home() {
  const session = await getSession();
  if (!session.operator) redirect("/login");

  const resp = await controlPlane.get("/strategies");
  const rows: Row[] = resp.ok ? await resp.json() : [];

  return (
    <main style={{ padding: 24 }}>
      <h1>Strategies</h1>
      <table style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr><th align="left">Name</th><th align="left">Status</th><th align="right">Weight</th><th /></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td style={{ padding: 6 }}>{r.name}</td>
              <td style={{ padding: 6 }}><StatusBadge status={r.status} /></td>
              <td style={{ padding: 6 }} align="right">{r.capital_weight.toFixed(2)}</td>
              <td style={{ padding: 6 }}><Link href={`/strategy/${r.id}`}>details</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 3: Create the detail page**

`dashboard/src/app/strategy/[id]/page.tsx`:
```tsx
import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";
import { redirect } from "next/navigation";
import StatusBadge from "@/components/StatusBadge";
import PnLSparkline from "@/components/PnLSparkline";
import PromoteButton from "./PromoteButton";
import DemoteButton from "./DemoteButton";
import StartPaperButton from "./StartPaperButton";

export default async function Detail({ params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  const { id } = await params;

  const [sResp, mResp] = await Promise.all([
    controlPlane.get(`/strategies?status=draft,backtest,paper,live,retired`),
    controlPlane.get(`/metrics/${id}`),
  ]);
  const rows = (await sResp.json()) as Array<{ id: number; name: string; status: string; paper_started_at: string | null; promoted_at: string | null }>;
  const row = rows.find((r) => r.id === Number(id));
  const metrics = (await mResp.json()) as Array<{ pnl: number }>;

  if (!row) return <main style={{ padding: 24 }}>Not found.</main>;

  return (
    <main style={{ padding: 24 }}>
      <h1>{row.name}</h1>
      <p>Status: <StatusBadge status={row.status} /></p>
      <p>Paper started: {row.paper_started_at ?? "—"}</p>
      <p>Promoted at: {row.promoted_at ?? "—"}</p>
      <p>PnL trend:</p>
      <PnLSparkline series={metrics.map((m) => m.pnl)} />
      <div style={{ marginTop: 24 }}>
        {row.status === "draft" ? <StartPaperButton id={row.id} /> : null}
        {row.status === "paper" ? <PromoteButton id={row.id} /> : null}
        {row.status === "live" ? <DemoteButton id={row.id} /> : null}
      </div>
    </main>
  );
}
```

`dashboard/src/app/strategy/[id]/PromoteButton.tsx`:
```tsx
"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function PromoteButton({ id }: { id: number }) {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  async function onClick() {
    const resp = await fetch(`/api/control/strategies/${id}/promote`, { method: "POST" });
    if (resp.ok) router.refresh();
    else setErr(await resp.text());
  }
  return (
    <>
      <button onClick={onClick} style={{ background: "#10b981", color: "white", padding: "8px 14px", border: 0, borderRadius: 6 }}>
        Promote to live
      </button>
      {err ? <pre style={{ color: "red" }}>{err}</pre> : null}
    </>
  );
}
```

`dashboard/src/app/strategy/[id]/DemoteButton.tsx`:
```tsx
"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function DemoteButton({ id }: { id: number }) {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  async function onClick() {
    if (!confirm("Demote this strategy from live back to paper?")) return;
    const resp = await fetch(`/api/control/strategies/${id}/demote`, { method: "POST" });
    if (resp.ok) router.refresh();
    else setErr(await resp.text());
  }
  return (
    <>
      <button onClick={onClick} style={{ background: "#ef4444", color: "white", padding: "8px 14px", border: 0, borderRadius: 6 }}>
        Demote to paper
      </button>
      {err ? <pre style={{ color: "red" }}>{err}</pre> : null}
    </>
  );
}
```

`dashboard/src/app/strategy/[id]/StartPaperButton.tsx`:
```tsx
"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function StartPaperButton({ id }: { id: number }) {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  async function onClick() {
    const resp = await fetch(`/api/control/strategies/${id}/start_paper`, { method: "POST" });
    if (resp.ok) router.refresh();
    else setErr(await resp.text());
  }
  return (
    <>
      <button onClick={onClick} style={{ background: "#f59e0b", color: "white", padding: "8px 14px", border: 0, borderRadius: 6 }}>
        Start paper trading
      </button>
      {err ? <pre style={{ color: "red" }}>{err}</pre> : null}
    </>
  );
}
```

- [ ] **Step 4: Type-check and build**

Run:
```bash
cd dashboard
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/app/page.tsx \
        dashboard/src/app/strategy/[id]/page.tsx \
        dashboard/src/app/strategy/[id]/PromoteButton.tsx \
        dashboard/src/app/strategy/[id]/DemoteButton.tsx \
        dashboard/src/app/strategy/[id]/StartPaperButton.tsx \
        dashboard/src/components/StatusBadge.tsx \
        dashboard/src/components/PnLSparkline.tsx
git commit -m "Render strategies list and detail with start-paper, promote, and demote actions"
```

---

### Task 21: `dashboard` audit page + settings page + global kill switch

**Files:**
- Create: `dashboard/src/app/audit/page.tsx`
- Create: `dashboard/src/app/settings/page.tsx`
- Create: `dashboard/src/components/KillSwitch.tsx`

**Interfaces:**
- Consumes: `/api/control/audit`, `/api/control/kill_all`.
- Produces: two rendered pages plus a client-side kill button.

- [ ] **Step 1: Implement `KillSwitch`**

`dashboard/src/components/KillSwitch.tsx`:
```tsx
"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function KillSwitch() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function onClick() {
    if (!confirm("Demote EVERY live strategy back to paper. Continue?")) return;
    setBusy(true);
    const resp = await fetch("/api/control/kill_all", { method: "POST" });
    setBusy(false);
    if (resp.ok) {
      const body = await resp.json();
      setMsg(`Demoted: ${body.demoted.join(", ") || "none"}`);
      router.refresh();
    } else {
      setMsg(await resp.text());
    }
  }
  return (
    <>
      <button onClick={onClick} disabled={busy} style={{ background: "#b91c1c", color: "white", padding: "10px 16px", border: 0, borderRadius: 6, fontWeight: 600 }}>
        {busy ? "Working…" : "KILL ALL LIVE STRATEGIES"}
      </button>
      {msg ? <p>{msg}</p> : null}
    </>
  );
}
```

- [ ] **Step 2: Create the audit page**

`dashboard/src/app/audit/page.tsx`:
```tsx
import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";
import { redirect } from "next/navigation";

interface Entry { id: number; ts: string; actor: string; action: string; payload_json: string }

export default async function Audit() {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  const resp = await controlPlane.get("/audit?limit=200");
  const entries: Entry[] = resp.ok ? await resp.json() : [];
  return (
    <main style={{ padding: 24 }}>
      <h1>Audit log</h1>
      <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
        <thead><tr><th align="left">Time</th><th align="left">Actor</th><th align="left">Action</th><th align="left">Payload</th></tr></thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td style={{ padding: 4 }}>{e.ts}</td>
              <td style={{ padding: 4 }}>{e.actor}</td>
              <td style={{ padding: 4 }}>{e.action}</td>
              <td style={{ padding: 4, maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis" }}>{e.payload_json}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 3: Create the settings page**

`dashboard/src/app/settings/page.tsx`:
```tsx
import { getSession } from "@/lib/session";
import { redirect } from "next/navigation";
import KillSwitch from "@/components/KillSwitch";

export default async function Settings() {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  return (
    <main style={{ padding: 24 }}>
      <h1>Settings</h1>
      <p>Trading mode: <code>{process.env.TRADING_MODE ?? "paper"}</code></p>
      <hr />
      <h2>Kill switch</h2>
      <KillSwitch />
    </main>
  );
}
```

- [ ] **Step 4: Type-check**

Run: `cd dashboard && npx tsc --noEmit`

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/app/audit/page.tsx \
        dashboard/src/app/settings/page.tsx \
        dashboard/src/components/KillSwitch.tsx
git commit -m "Add audit page, settings page, and global kill switch"
```

---

### Task 22: `dashboard` Dockerfile + compose entry

**Files:**
- Create: `dashboard/Dockerfile`
- Create: `dashboard/.dockerignore`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: env from `.env`.
- Produces: `dashboard` service on the compose network at internal port `3000`, published to `3000` on the host.

- [ ] **Step 1: Create `.dockerignore` and `Dockerfile`**

`dashboard/.dockerignore`:
```
node_modules
.next
.git
```

`dashboard/Dockerfile`:
```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public 2>/dev/null || true
EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 2: Add to `docker-compose.yml`**

Append under `services:`:
```yaml
  dashboard:
    build:
      context: ./dashboard
    networks:
      - decipher
    env_file:
      - .env
    environment:
      - CONTROL_PLANE_URL=http://control-plane:8000
    depends_on:
      control-plane:
        condition: service_healthy
    ports:
      - "3000:3000"
```

- [ ] **Step 3: Verify build**

```bash
docker compose build dashboard
```

Expected: image builds. Full flow tested in Task 24.

- [ ] **Step 4: Commit**

```bash
git add dashboard/Dockerfile dashboard/.dockerignore docker-compose.yml
git commit -m "Containerize dashboard and publish port 3000"
```

---

### Task 23: Root docker-compose overrides + `.env.example` polish + operator runbook

**Files:**
- Create: `docker-compose.paper.yml`
- Create: `docker-compose.live.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `.env`.
- Produces: two override files enforcing G2 at the compose layer, and an operator-runbook README section.

- [ ] **Step 1: Create paper override**

`docker-compose.paper.yml`:
```yaml
services:
  control-plane:
    environment:
      - TRADING_MODE=paper
  nautilus-runner:
    environment:
      - TRADING_MODE=paper
      # Mainnet credentials deliberately left unset; testnet fields flow in from .env.
      - HYPERLIQUID_MAINNET_PRIVATE_KEY=
      - HYPERLIQUID_MAINNET_ACCOUNT_ID=
```

- [ ] **Step 2: Create live override**

`docker-compose.live.yml`:
```yaml
services:
  control-plane:
    environment:
      - TRADING_MODE=live
  nautilus-runner:
    environment:
      - TRADING_MODE=live
      # Live keys pulled from .env; unset testnet fields so a mistake never routes mainnet flow to testnet URLs.
      - HYPERLIQUID_TESTNET_PRIVATE_KEY=
      - HYPERLIQUID_TESTNET_ACCOUNT_ID=
```

- [ ] **Step 3: Rewrite `README.md`**

```markdown
# decipher-trader

Self-hosted crypto trading system built on NautilusTrader and Hyperliquid. Runs strategies against Hyperliquid testnet by default; requires an explicit override plus a per-strategy human promotion before it will touch mainnet.

## Prerequisites

- Docker Engine 24+ with Compose v2.
- A `.env` file (copy from `.env.example`) with:
  - `OPERATOR_TOKEN` — long random string. Every mutating REST call needs it.
  - `DASHBOARD_SESSION_SECRET` — 32+ char random string.
  - `DASHBOARD_OPERATOR_USERNAME` / `DASHBOARD_OPERATOR_PASSWORD_BCRYPT` — dashboard login. Generate the bcrypt hash locally with any bcrypt tool.
  - `HYPERLIQUID_TESTNET_PRIVATE_KEY`, `HYPERLIQUID_TESTNET_ACCOUNT_ID` for paper mode.
  - `HYPERLIQUID_MAINNET_PRIVATE_KEY`, `HYPERLIQUID_MAINNET_ACCOUNT_ID` for live mode.

## Run in paper mode (default)

```bash
docker compose -f docker-compose.yml -f docker-compose.paper.yml up --build
```

Visit `http://localhost:3000`, log in, and register a strategy. Nautilus-runner will only see strategies with `status=paper` and will trade them against Hyperliquid testnet.

## Promote to live mode

Live mode requires that at least one strategy has already been promoted through the dashboard (`Promote to live` on its detail page). The promote button is gated in the backend: it will refuse until the strategy has been in paper mode for at least 14 days with no snapshot showing drawdown greater than its `max_daily_loss`.

Only after promotion, stop the paper stack and start the live stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.paper.yml down
docker compose -f docker-compose.yml -f docker-compose.live.yml up --build
```

The runner refuses to start in live mode if any `status=live` row lacks `promoted_at` / `promoted_by` metadata.

## Kill switch

- Global: dashboard → Settings → KILL ALL LIVE STRATEGIES.
- Per-strategy: dashboard → strategy detail → Demote to paper.
- Programmatic: `POST /kill_all` on control-plane with `Authorization: Bearer $OPERATOR_TOKEN`.

Both paths demote the row(s) and broadcast a `kill_all` event over the websocket, which nautilus-runner honours by stopping the node.

## Backup and portability

State is in two named docker volumes: `decipher-db` (SQLite) and `decipher-cache` (backtest data). Back up:

```bash
docker run --rm -v decipher-trader_decipher-db:/data -v $PWD:/backup alpine tar czf /backup/decipher-db.tgz -C /data .
```

Restore on any host by untar-ing into the same volume before `docker compose up`.
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.paper.yml docker-compose.live.yml README.md
git commit -m "Add paper and live compose overrides and operator runbook"
```

---

### Task 24: End-to-end smoke test with mocked Hyperliquid

**Files:**
- Create: `e2e/docker-compose.e2e.yml`
- Create: `e2e/mock_hyperliquid.py`
- Create: `e2e/fixtures/hyperliquid_ws.json`
- Create: `e2e/smoke.py`
- Modify: `.github/workflows/e2e.yml` (only if the user chooses to add CI; skip if not desired)

**Interfaces:**
- Consumes: all three services.
- Produces: a scripted proof that (a) control-plane comes up healthy, (b) a strategy can be created and its status advanced to `paper` via the operator token, (c) `/promote` refuses without the 14-day gate and succeeds with a fixture-seeded `paper_started_at`, (d) `/kill_all` demotes every live row and broadcasts the event, (e) nautilus-runner container starts and exits cleanly against a mocked venue.

- [ ] **Step 1: Create the Hyperliquid mock**

`e2e/mock_hyperliquid.py`:
```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket

app = FastAPI()
FIXTURE = Path(__file__).parent / "fixtures" / "hyperliquid_ws.json"


@app.get("/info")
async def info() -> dict:
    return {"status": "ok"}


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    events = json.loads(FIXTURE.read_text())
    for ev in events:
        await sock.send_text(json.dumps(ev))
        await asyncio.sleep(0.05)
    while True:
        await asyncio.sleep(60)
```

`e2e/fixtures/hyperliquid_ws.json`:
```json
[
  {"channel": "subscriptionResponse", "data": "ok"},
  {"channel": "trades", "data": {"coin": "BTC", "px": "60000", "sz": "0.01", "time": 1735689600000}}
]
```

- [ ] **Step 2: Create the compose file**

`e2e/docker-compose.e2e.yml`:
```yaml
name: decipher-e2e
services:
  mock-hyperliquid:
    build:
      context: .
      dockerfile_inline: |
        FROM python:3.12-slim
        WORKDIR /app
        RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn==0.32.0
        COPY mock_hyperliquid.py ./mock_hyperliquid.py
        COPY fixtures ./fixtures
        CMD ["uvicorn", "mock_hyperliquid:app", "--host", "0.0.0.0", "--port", "9000"]
    networks:
      - decipher
    ports:
      - "9000:9000"

networks:
  decipher:
    external: true
    name: decipher-trader_decipher
```

- [ ] **Step 3: Write the smoke driver**

`e2e/smoke.py`:
```python
#!/usr/bin/env python3
"""Drive the full paper→live promotion flow. Run after `docker compose up -d`."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://localhost:8000"
TOKEN = os.environ["OPERATOR_TOKEN"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _wait_healthy(timeout_s: int = 30) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("control-plane never became healthy")


def main() -> int:
    _wait_healthy()

    r = httpx.post(
        f"{BASE}/strategies",
        headers=AUTH,
        json={"name": "smoke_toy", "code_path": "strategies/toy_momentum/strategy.py", "max_notional": 100, "max_daily_loss": 10, "max_position": 1},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # G7: promotion must refuse from draft.
    r = httpx.post(f"{BASE}/strategies/{sid}/promote", headers=AUTH)
    assert r.status_code == 409, r.text

    # Transition to paper via the API.
    r = httpx.post(f"{BASE}/strategies/{sid}/start_paper", headers=AUTH)
    assert r.status_code == 200, r.text

    # G7: promotion still refuses — paper_started_at is `now`, not 14 days ago.
    r = httpx.post(f"{BASE}/strategies/{sid}/promote", headers=AUTH)
    assert r.status_code == 409, r.text
    assert "14" in r.json()["detail"]

    # Back-date `paper_started_at` by 20 days to satisfy G7. This is an E2E-only shortcut:
    # in real operation the operator waits the full 14 days. We do the DB touch through a
    # bind-mounted SQLite file that the compose override in Step 4 sets up.
    import sqlite3
    conn = sqlite3.connect("/tmp/decipher-e2e/decipher.sqlite3")
    try:
        conn.execute(
            "UPDATE strategies SET paper_started_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(days=20)).isoformat(), sid),
        )
        conn.commit()
    finally:
        conn.close()

    # G7 pass: 20 days > 14, no drawdown snapshots.
    r = httpx.post(f"{BASE}/strategies/{sid}/promote", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "live"

    # G4: kill_all demotes.
    r = httpx.post(f"{BASE}/kill_all", headers=AUTH)
    assert r.status_code == 200, r.text
    assert sid in r.json()["demoted"]

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the smoke test locally**

```bash
cp .env.example .env
mkdir -p /tmp/decipher-e2e
# Override the compose DB path to a host-visible location for the E2E only:
cat > docker-compose.e2e-override.yml <<EOF
services:
  control-plane:
    environment:
      - CONTROL_PLANE_DB_PATH=/data/decipher.sqlite3
    volumes:
      - /tmp/decipher-e2e:/data
EOF

docker compose -f docker-compose.yml -f docker-compose.paper.yml -f docker-compose.e2e-override.yml up -d control-plane
python3 -m venv /tmp/decipher-e2e-venv && /tmp/decipher-e2e-venv/bin/pip install httpx
OPERATOR_TOKEN=$(grep -E '^OPERATOR_TOKEN=' .env | cut -d= -f2) /tmp/decipher-e2e-venv/bin/python e2e/smoke.py
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f docker-compose.e2e-override.yml down
```

Expected: `SMOKE OK`.

- [ ] **Step 5: Add a README section pointing to the E2E**

Append to `README.md`:
```markdown

## End-to-end smoke test

See `e2e/smoke.py`. Requires Docker; drives the full paper→live promotion flow with the operator token, then kills.
```

- [ ] **Step 6: Commit**

```bash
git add e2e/docker-compose.e2e.yml e2e/mock_hyperliquid.py e2e/fixtures/hyperliquid_ws.json e2e/smoke.py README.md
git commit -m "Add end-to-end smoke test driving the promotion and kill paths"
```

---

## Phase 1 Exit Criteria

The plan is complete when all of the following are true, verified from a fresh checkout:

1. `docker compose -f docker-compose.yml -f docker-compose.paper.yml up --build` starts `control-plane`, `nautilus-runner`, and `dashboard`. All three services report healthy.
2. The operator logs into the dashboard, creates a strategy, and sees it as `draft`.
3. The operator clicks `Start paper trading`; the dashboard shows the strategy as `paper` with a fresh `paper_started_at`.
4. Clicking `Promote to live` succeeds when the 14-day + no-fatal-drawdown gate is satisfied, and rejects with a helpful message otherwise (both cases verified in the audit log).
5. Clicking the global `KILL ALL LIVE STRATEGIES` button demotes every live row and broadcasts a `kill_all` event; `nautilus-runner` receives the event and stops.
6. `e2e/smoke.py` prints `SMOKE OK` end-to-end.
7. Full test suite passes for each service: `cd control-plane && uv run pytest`; `cd nautilus-runner && uv run pytest`; `cd dashboard && npm test`; `cd dashboard && npx tsc --noEmit`.
8. `git status` is clean.

## Deferred (out of scope for Phase 1)

**To Phase 2:**
- `agent-service` container and Anthropic Claude client.
- LLM sandbox pipeline (G6).
- News provider adapters (CryptoPanic + CoinDesk RSS) and corroboration rule.
- Backtest runner job that advances `draft` → `paper` automatically when thresholds pass.
- LLM strategy-author dashboard review page.

**Small Phase 1.5 follow-ups (tracked, not blocking Phase 1 exit):**
- Chaos tests from spec §9 (kill nautilus-runner mid-order and assert audit log intent; kill control-plane and assert nautilus-runner refuses further orders until reconnect).
- Full Nautilus backtest fixture for `ToyMomentum` (unskips the test placeholder in Task 14).
- Plumbing per-strategy caps into Nautilus `RiskEngine` in addition to the in-strategy check, once the RiskEngine per-strategy API is investigated.
