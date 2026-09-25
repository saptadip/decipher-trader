"""Regression fence: keep StreamingFeatherWriter out of the live runner.

Rationale — Nautilus rc5's ``StreamingFeatherWriter.subscribe()`` installs a
msgbus callback whose internal Rust path calls ``Tokio::block_on()``. When the
callback fires from inside the ``LiveNode``'s Tokio runtime, the nested
``block_on`` collides with the outer runtime and the whole process panics:

    thread '<unnamed>' (1) panicked at
    crates/persistence/src/backend/feather.rs:1038:41:
    Cannot start a runtime from within a runtime.

Reproduced with ``RUST_BACKTRACE=full``; the panic fires with an
``include_types=[]`` filter, with a bogus filter, and immediately at
data-client connect regardless of subscription content. The runner boots
cleanly once ``subscribe()`` is disabled and the writer is not constructed
(verified end-to-end: mass status, portfolio init, trader started).

Re-enabling ``StreamingFeatherWriter`` in ``main.py`` on rc5 will bring the
panic back. The correct restoration path is ``StreamingConfig`` wired on
``LiveNodeBuilder`` under Nautilus 2.0 stable — see the deferred item in
PROGRESS.md.
"""

from pathlib import Path

RUNNER_SRC = Path(__file__).resolve().parents[1] / "src"
MAIN_PY = RUNNER_SRC / "nautilus_runner" / "main.py"


def _runner_python_sources() -> list[Path]:
    return sorted(RUNNER_SRC.rglob("*.py"))


def test_runner_tree_does_not_construct_streaming_feather_writer():
    """Fence guards the whole runner package, not just main.py.

    The panic is a runtime property of any ``StreamingFeatherWriter.subscribe()``
    call reached from inside the LiveNode Tokio runtime — it does not matter
    which Python module makes the call. Scan every source file under
    ``nautilus_runner`` and require zero construction sites.
    """
    offenders = []
    for path in _runner_python_sources():
        src = path.read_text()
        if "StreamingFeatherWriter(" in src:
            offenders.append(str(path.relative_to(RUNNER_SRC)))
    assert not offenders, (
        f"StreamingFeatherWriter constructed in: {offenders}. "
        "See tests/test_main_no_streaming_feather_writer.py docstring for the "
        "rc5 panic reproduction and the 2.0-stable restoration path."
    )


def test_runner_tree_does_not_import_streaming_feather_writer():
    offenders = []
    for path in _runner_python_sources():
        src = path.read_text()
        if (
            "from nautilus_trader.persistence import StreamingFeatherWriter" in src
            or "import StreamingFeatherWriter" in src
        ):
            offenders.append(str(path.relative_to(RUNNER_SRC)))
    assert not offenders, f"StreamingFeatherWriter imported in: {offenders}"


def test_main_carries_the_disable_tombstone_comment():
    """main.py must keep the explainer so a future maintainer knows WHY the
    writer is gone. Assertion is on a stable substring, not the whole comment."""
    src = MAIN_PY.read_text()
    assert "StreamingFeatherWriter" in src, (
        "main.py must retain the disable tombstone comment explaining why the "
        "writer is not wired on rc5 (see tests/test_main_no_streaming_feather_writer.py)."
    )
