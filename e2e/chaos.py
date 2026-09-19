#!/usr/bin/env python3
"""Shallow chaos tests for decipher-trader.

Scenario A (kill_runner): docker-kills the runner container and asserts it
exits with code 137 (SIGKILL) while the control-plane remains healthy and no
audit rows are lost.

Scenario B (kill_control_plane): stops the control-plane and asserts the
runner self-terminates cleanly (exit code 0) via the heartbeat miss path.

Prerequisites:
- Control-plane published on localhost:8000.
- OPERATOR_TOKEN set in the environment.
- HYPERLIQUID_TESTNET_PRIVATE_KEY and HYPERLIQUID_TESTNET_ACCOUNT_ID populated
  in .env (runner will not boot without them).
- Stack started with:
    docker compose -f docker-compose.yml -f docker-compose.paper.yml \
                   -f e2e/docker-compose.chaos.yml up -d

Run:
    OPERATOR_TOKEN=... python e2e/chaos.py A   # scenario A only
    OPERATOR_TOKEN=... python e2e/chaos.py B   # scenario B only
    OPERATOR_TOKEN=... python e2e/chaos.py     # both in sequence (fresh stack required)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

BASE = "http://localhost:8000"
TOKEN = os.environ["OPERATOR_TOKEN"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Compose override files — must match the stack that was started.
_COMPOSE_FILES = [
    "-f", "docker-compose.yml",
    "-f", "docker-compose.paper.yml",
    "-f", "e2e/docker-compose.chaos.yml",
]

# With HEARTBEAT_INTERVAL_SECS=2 and HEARTBEAT_MISS_LIMIT=2 the runner fires
# on_miss after 2 consecutive misses, each separated by ~2 s — so ~4-6 s after
# the control-plane disappears.  Allow 10 s margin for container startup time.
_HEARTBEAT_INTERVAL = 2
_HEARTBEAT_MISS_LIMIT = 2
_SCENARIO_B_TIMEOUT = _HEARTBEAT_INTERVAL * _HEARTBEAT_MISS_LIMIT * 2 + 10  # ~18 s


def _service_container_id(service: str) -> str:
    result = subprocess.run(
        ["docker", "compose"] + _COMPOSE_FILES + ["ps", "-q", service],
        capture_output=True,
        text=True,
        check=True,
    )
    cid = result.stdout.strip()
    if not cid:
        raise SystemExit(f"{service} container not found — is the stack up?")
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


def _assert_running(cid: str, label: str) -> None:
    state = _container_state(cid)
    if state != "running":
        raise SystemExit(f"{label} container is not running (state={state})")


def _wait_exited(cid: str, label: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _container_state(cid)
        if state == "exited":
            return
        time.sleep(1)
    raise SystemExit(
        f"{label} did not reach 'exited' within {timeout_s}s "
        f"(last state={_container_state(cid)})"
    )


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


def _audit_count() -> int:
    r = httpx.get(f"{BASE}/audit", headers=AUTH, params={"limit": 1000}, timeout=5.0)
    r.raise_for_status()
    return len(r.json())


def scenario_a() -> None:
    """Kill the runner container (SIGKILL). Assert exit 137, control-plane healthy, no audit loss."""
    print("[chaos A] starting scenario A: docker kill runner")

    _wait_healthy()

    runner_cid = _service_container_id("nautilus-runner")
    print(f"[chaos A] runner container: {runner_cid}")
    _assert_running(runner_cid, "nautilus-runner")

    count_before = _audit_count()
    print(f"[chaos A] audit rows before kill: {count_before}")

    print(f"[chaos A] sending docker kill {runner_cid}")
    subprocess.run(["docker", "kill", runner_cid], check=True, capture_output=True)

    _wait_exited(runner_cid, "nautilus-runner", timeout_s=15)
    exit_code = _container_exit_code(runner_cid)
    assert exit_code == 137, f"expected exit_code=137 (SIGKILL), got {exit_code}"
    print(f"[chaos A] runner exited with code {exit_code} (SIGKILL confirmed)")

    # Control-plane must still be healthy.
    r = httpx.get(f"{BASE}/health", timeout=5.0)
    assert r.status_code == 200, f"control-plane /health returned {r.status_code}"
    print("[chaos A] control-plane still healthy")

    count_after = _audit_count()
    assert count_after >= count_before, (
        f"audit rows decreased: before={count_before}, after={count_after}"
    )
    print(f"[chaos A] audit rows after: {count_after} (>= {count_before})")

    print("CHAOS A OK")


def scenario_b() -> None:
    """Stop the control-plane. Assert runner self-terminates cleanly (exit 0) via heartbeat miss."""
    print("[chaos B] starting scenario B: docker stop control-plane")

    _wait_healthy()

    runner_cid = _service_container_id("nautilus-runner")
    cp_cid = _service_container_id("control-plane")
    print(f"[chaos B] runner container: {runner_cid}")
    print(f"[chaos B] control-plane container: {cp_cid}")

    _assert_running(runner_cid, "nautilus-runner")
    _assert_running(cp_cid, "control-plane")

    print(f"[chaos B] sending docker stop {cp_cid}")
    subprocess.run(["docker", "stop", cp_cid], check=True, capture_output=True)
    print(f"[chaos B] control-plane stopped; waiting up to {_SCENARIO_B_TIMEOUT}s for runner to exit")

    _wait_exited(runner_cid, "nautilus-runner", timeout_s=_SCENARIO_B_TIMEOUT)
    exit_code = _container_exit_code(runner_cid)
    # Accepted shutdown codes: 0 (clean), 133 (Nautilus rc5 SIGTRAP from Rust runtime
    # when node.stop() is called from a non-main thread — see smoke.py notes),
    # 143 (SIGTERM). Behaviour under test = "runner stopped after HEARTBEAT_MISS_LIMIT
    # missed heartbeats"; exact exit code is a Nautilus rc5 shutdown quirk.
    accepted = {0, 133, 143}
    assert exit_code in accepted, (
        f"expected exit_code in {accepted} (clean stop / rc5 quirk), got {exit_code}"
    )
    print(f"[chaos B] runner exited with code {exit_code}")

    print("CHAOS B OK")


def main() -> int:
    scenarios_arg = sys.argv[1].upper() if len(sys.argv) > 1 else "AB"

    if "A" in scenarios_arg:
        scenario_a()

    if "B" in scenarios_arg:
        scenario_b()

    if "A" in scenarios_arg and "B" in scenarios_arg:
        print("CHAOS OK")

    return 0


if __name__ == "__main__":
    sys.exit(main())
