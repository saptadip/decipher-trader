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

MAIN_PY = (
    Path(__file__).resolve().parents[1] / "src" / "nautilus_runner" / "main.py"
)


def test_main_does_not_construct_streaming_feather_writer():
    src = MAIN_PY.read_text()
    # The class name may appear inside doc-comments explaining the panic; only
    # the call/construction site is forbidden.
    assert "StreamingFeatherWriter(" not in src, (
        "StreamingFeatherWriter must not be constructed in main.py on rc5 — "
        "its subscribe() msgbus callback panics with a nested Tokio block_on. "
        "See the docstring on this test file."
    )


def test_main_does_not_import_streaming_feather_writer():
    src = MAIN_PY.read_text()
    assert "from nautilus_trader.persistence import StreamingFeatherWriter" not in src
    assert "import StreamingFeatherWriter" not in src
