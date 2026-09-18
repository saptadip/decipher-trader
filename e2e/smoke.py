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
