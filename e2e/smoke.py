#!/usr/bin/env python3
"""Drive the full paper→live promotion flow. Run after `docker compose up -d`."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://localhost:8000"
TOKEN = os.environ["OPERATOR_TOKEN"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Compose override files used for the smoke run — used to resolve container IDs.
_COMPOSE_FILES = [
    "-f", "docker-compose.yml",
    "-f", "docker-compose.paper.yml",
    "-f", "e2e/docker-compose.smoke.yml",
]


def _runner_container_id() -> str:
    """Return the container ID for the nautilus-runner service."""
    result = subprocess.run(
        ["docker", "compose"] + _COMPOSE_FILES + ["ps", "-q", "nautilus-runner"],
        capture_output=True,
        text=True,
        check=True,
    )
    cid = result.stdout.strip()
    if not cid:
        raise SystemExit("nautilus-runner container not found — is it included in the compose up?")
    return cid


def _container_state(cid: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", cid],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _container_exit_code(cid: str) -> int:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.ExitCode}}", cid],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def _wait_runner_running(cid: str, timeout_s: int = 30) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _container_state(cid)
        if state == "running":
            return
        if state in ("exited", "dead"):
            raise SystemExit(f"nautilus-runner container already stopped (state={state}) before kill_all was sent")
        time.sleep(1)
    raise SystemExit(f"nautilus-runner did not reach 'running' state within {timeout_s}s (last state={_container_state(cid)})")


def _wait_runner_exited(cid: str, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _container_state(cid)
        if state == "exited":
            return
        time.sleep(2)
    raise SystemExit(f"nautilus-runner did not reach 'exited' state within {timeout_s}s (last state={_container_state(cid)})")


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

    strategy_name = f"smoke_toy_{int(time.time())}"
    r = httpx.post(
        f"{BASE}/strategies",
        headers=AUTH,
        json={"name": strategy_name, "code_path": "strategies/toy_momentum/strategy.py", "max_notional": 100, "max_daily_loss": 10, "max_position": 1},
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
    data_dir = os.environ.get("SMOKE_DATA_DIR", "/tmp/decipher-e2e")
    conn = sqlite3.connect(f"{data_dir}/decipher.sqlite3")
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

    # --- nautilus-runner verification ---
    # Resolve the runner container before sending kill_all. The runner must be
    # running at this point; it started (via depends_on: service_healthy) before
    # this script began but only fetches paper strategies at boot, so it sees an
    # empty strategy list — that's the desired shallow smoke behaviour.
    runner_cid = _runner_container_id()
    print(f"[runner] container id: {runner_cid}")
    _wait_runner_running(runner_cid, timeout_s=30)
    print(f"[runner] state=running confirmed before kill_all")

    # G4: kill_all demotes strategies AND broadcasts the kill event to the runner.
    r = httpx.post(f"{BASE}/kill_all", headers=AUTH)
    assert r.status_code == 200, r.text
    assert sid in r.json()["demoted"]

    # After kill_all the runner's kill_listener_loop receives {"type":"kill_all"},
    # calls node.stop(), and the process exits. Give it up to 60 s.
    # Accepted exit codes:
    #   0   — clean shutdown
    #   133 — SIGTRAP from Nautilus Rust runtime during pyo3 shutdown; observed with
    #         node.stop() called from a non-main thread on nautilus-trader 2.0.0rc5
    #   137 — SIGKILL (container was forcibly killed)
    #   143 — SIGTERM (container received termination signal)
    # Behaviour under test = "runner stopped trading in response to kill_all"; the
    # exact exit code is a Nautilus rc5 shutdown quirk, not a behaviour defect.
    print("[runner] waiting for container to reach 'exited' state (up to 60 s)…")
    _wait_runner_exited(runner_cid, timeout_s=60)
    exit_code = _container_exit_code(runner_cid)
    accepted = {0, 133, 137, 143}
    assert exit_code in accepted, (
        f"nautilus-runner exited with unexpected code: {exit_code} (accepted: {accepted})"
    )
    print(f"[runner] exited (exit_code={exit_code})")

    print("SMOKE OK (with runner)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
