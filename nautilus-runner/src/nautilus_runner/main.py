from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidDataClientFactory,
    HyperliquidExecutionClientConfig,
    HyperliquidExecutionClientFactory,
)
from nautilus_trader.common import Environment
from nautilus_trader.live import LiveExecutionEngineConfig, LiveNode, LiveRiskEngineConfig
from nautilus_trader.model import AccountId, BarType, InstrumentId, StrategyId, TraderId

from nautilus_runner import state
from nautilus_runner.config import (
    RunnerSettings,
    assert_live_startup_safe,
    build_risk_caps,
    hyperliquid_env_for,
)
from nautilus_runner.control_plane_client import ControlPlaneClient
from nautilus_runner.heartbeat import heartbeat_loop
from nautilus_runner.kill_listener import kill_listener_loop
from nautilus_runner.state import AuditWriter, MetricsWriter
from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig

HYPERLIQUID = "HYPERLIQUID"


async def _fetch_strategies(settings: RunnerSettings) -> list[dict]:
    client = ControlPlaneClient(settings.control_plane_url, settings.operator_token)
    wanted = ["paper"] if settings.trading_mode == "paper" else ["live"]
    return await client.fetch_strategies(wanted)


def _build_node(settings: RunnerSettings, strategy_rows: list[dict]) -> LiveNode:
    env = hyperliquid_env_for(settings.trading_mode)
    account_id_str = (
        settings.testnet_account_id
        if settings.trading_mode == "paper"
        else settings.mainnet_account_id
    )
    private_key = (
        settings.testnet_private_key
        if settings.trading_mode == "paper"
        else settings.mainnet_private_key
    )
    if not account_id_str or not private_key:
        raise RuntimeError(
            f"missing Hyperliquid credentials for mode={settings.trading_mode}"
        )

    # R1: bypass=True — no per-instrument RiskEngine config in Phase 1; G3 enforced at
    # the strategy layer via ToyMomentum._submit_capped.
    node = (
        LiveNode.builder(
            f"DECIPHER-{settings.trading_mode.upper()}",
            TraderId.from_str(settings.trader_id),
            Environment.LIVE,
        )
        .with_reconciliation(reconciliation=True)
        .with_risk_engine_config(LiveRiskEngineConfig(bypass=True))
        .with_exec_engine_config(LiveExecutionEngineConfig(position_check_interval_secs=300.0))
        .add_data_client(
            None,
            HyperliquidDataClientFactory(),
            HyperliquidDataClientConfig(environment=env, private_key=private_key),
        )
        .add_exec_client(
            None,
            HyperliquidExecutionClientFactory(),
            HyperliquidExecutionClientConfig(
                account_id=AccountId(account_id_str),
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
            strategy_db_id=row["id"],
        )
        node.add_strategy(ToyMomentum(cfg))

    return node


def main() -> None:
    import signal
    import threading

    settings = RunnerSettings()
    state.metrics = MetricsWriter(settings.control_plane_url, settings.operator_token)
    state.audit_writer = AuditWriter(settings.control_plane_url, settings.operator_token)
    rows = asyncio.run(_fetch_strategies(settings))
    assert_live_startup_safe(rows, settings.trading_mode)

    # I4: create the streaming catalog directory eagerly with a friendly error so an
    # unmounted decipher-cache volume produces an operator-actionable message rather
    # than a raw OSError at process start.
    try:
        os.makedirs(settings.streaming_catalog_path, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create streaming catalog directory {settings.streaming_catalog_path!r}; "
            "check that the decipher-cache volume is mounted and writable"
        ) from exc

    node = _build_node(settings, rows)

    # Live-mode trade-event persistence via StreamingFeatherWriter is disabled
    # in rc5. Its `subscribe()` installs a msgbus callback whose internal Rust
    # path calls `Tokio::block_on()`; when the callback fires from inside the
    # LiveNode's Tokio runtime, the nested block_on collides with the outer
    # runtime and panics ("Cannot start a runtime from within a runtime",
    # crates/persistence/src/backend/feather.rs:1038). Reproduced with
    # RUST_BACKTRACE=full: panics fire with an `include_types=[]` filter,
    # a bogus filter, and immediately at data-client connect regardless of
    # subscription content. Runner boots fully once `subscribe()` is disabled
    # (verified end-to-end: mass status, portfolio init, trader started).
    #
    # Restoration path (tracked in the PROGRESS.md "Nautilus 2.0 stable" note):
    # swap for `StreamingConfig` wired on `LiveNodeBuilder` once Nautilus 2.0
    # stable exposes it — rc5 has the config class but no LiveNode wiring. Until
    # then, trade history can be reconstructed from the control-plane audit log
    # + venue API history if needed.
    #
    # `settings.streaming_catalog_path` is still read+mkdir'd above so the env
    # var stays validated and the volume mount remains asserted — swapping to
    # `StreamingConfig` later needs the same catalog directory.

    stop_event = asyncio.Event()
    ws_url = (
        settings.control_plane_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )
        + "/events"
    )

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
                heartbeat_loop(
                    client,
                    settings.heartbeat_interval_secs,
                    settings.heartbeat_miss_limit,
                    stop_event,
                    _trigger_kill,
                ),
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
    # No feather_writer.close() here — the writer is disabled in rc5 (see the
    # comment block above `node = _build_node(...)` for the panic reproduction).
    loop.call_soon_threadsafe(stop_event.set)


if __name__ == "__main__":
    main()
