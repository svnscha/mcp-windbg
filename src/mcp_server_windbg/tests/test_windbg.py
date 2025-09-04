"""
Extended test-suite for the WinDbg-attach workflow in ``mcp_server_windbg.server``.

Each logical scenario now has *two* tests:

* a **monkey-patched** variant that uses ``DummyWinDbgSession`` (runs everywhere);
* a **live** variant that talks to a real WinDbg instance **iff** one is running
  with the MCP JS plug-in already loaded (`mcp_init`).

The live tests are automatically skipped when WinDbg is not available.
"""

from pathlib import Path
import json
import tempfile
import time
import uuid

import psutil
import pytest

import mcp_server_windbg.server as srv


# --------------------------------------------------------------------------- #
#                       helper: is a real WinDbg session up?                  #
# --------------------------------------------------------------------------- #
def _live_windbg_available() -> bool:
    """Return True when WinDbg+MCP can accept an attach on this host."""
    if srv.AttachWindbgParams is None:
        return False
    # Look for the modern WinDbg Preview host process (“dbgx.shell.exe”)
    return any(p.name().lower() == "dbgx.shell.exe" for p in psutil.process_iter(["name"]))


LIVE_AVAILABLE = _live_windbg_available()


# --------------------------------------------------------------------------- #
#                        Dummy implementation of WinDbgSession                #
# --------------------------------------------------------------------------- #
class DummyWinDbgSession:
    """Minimal replacement that records commands and owns a shutdown file."""

    def __init__(self, dump_path, *_, **__):
        # In attach mode the server passes dump_path=None
        assert dump_path is None
        self.dump_path = None
        self.session_id = f"dummy-windbg-{uuid.uuid4().hex}"
        self.commands: list[str] = []

        # fake shutdown file:  <TMP>/<session_id>_shutdown.json
        self.shutdown_file = (
            Path(tempfile.gettempdir()) / f"{self.session_id}_shutdown.json"
        )
        self._write_shutdown_file(int(time.time() * 1000))

    # ----- helpers --------------------------------------------------------- #
    def _write_shutdown_file(self, ts: int):
        with self.shutdown_file.open("w") as f:
            json.dump(
                {
                    "type": "shutdown",
                    "sessionId": self.session_id,
                    "timestamp": ts,
                },
                f,
            )

    # ----- public API expected by the server ------------------------------- #
    def send_command(self, cmd: str):
        self.commands.append(cmd)
        return [f"dummy-windbg-out for {cmd}"]

    def shutdown(self):
        """Simulate graceful tear-down by bumping the timestamp."""
        self.commands.append("shutdown")
        self._write_shutdown_file(int(time.time() * 1000) + 1)


# --------------------------------------------------------------------------- #
#                      automatic fixture – clear cache each test              #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_active_sessions():
    yield
    srv.active_sessions.clear()


# --------------------------------------------------------------------------- #
#                               PARAM SETS                                    #
# --------------------------------------------------------------------------- #
# “dummy” tests always run; “live” ones are auto-skipped when WinDbg missing.
dummy_only = pytest.mark.usefixtures("monkeypatch")
live_only = pytest.mark.skipif(
    not LIVE_AVAILABLE,
    reason="Real WinDbg not running or MCP plug-in not initialised",
)


# --------------------------------------------------------------------------- #
#                     1)  attach and cache integrity                          #
# --------------------------------------------------------------------------- #
@dummy_only
def test_attach_windbg_and_cache_dummy(monkeypatch):
    monkeypatch.setattr(srv, "WinDbgSession", DummyWinDbgSession)

    banner = srv.handle_attach_windbg(arguments={}, timeout=5, verbose=False)

    assert banner and "dummy-windbg-" in banner[0].text
    assert "__windbg_attach__" in srv.active_sessions
    assert isinstance(srv.active_sessions["__windbg_attach__"], DummyWinDbgSession)


@live_only
def test_attach_windbg_and_cache_live():
    banner = srv.handle_attach_windbg(arguments={}, timeout=10, verbose=False)

    assert banner and "Attached to WinDbg session" in banner[0].text
    assert "__windbg_attach__" in srv.active_sessions


# --------------------------------------------------------------------------- #
#                     2)  run command after attach                            #
# --------------------------------------------------------------------------- #
@dummy_only
def test_run_command_after_attach_dummy(monkeypatch):
    monkeypatch.setattr(srv, "WinDbgSession", DummyWinDbgSession)

    srv.handle_attach_windbg(arguments={}, timeout=5, verbose=False)
    sess = srv.active_sessions["__windbg_attach__"]

    out = sess.send_command(".lastevent")

    assert out == ["dummy-windbg-out for .lastevent"]
    assert ".lastevent" in sess.commands


@live_only
def test_run_command_after_attach_live():
    srv.handle_attach_windbg(arguments={}, timeout=10, verbose=False)
    sess = srv.active_sessions["__windbg_attach__"]

    out = sess.send_command(".lastevent")
    assert len(out) > 0      # WinDbg should return at least one line


# --------------------------------------------------------------------------- #
#                     3)  cleanup / session shutdown                          #
# --------------------------------------------------------------------------- #
@dummy_only
def test_cleanup_sessions_updates_shutdown_file_dummy(monkeypatch):
    """
    ``cleanup_sessions`` must call ``session.shutdown()`` (which bumps the
    timestamp) and then empty the global registry.
    """
    monkeypatch.setattr(srv, "WinDbgSession", DummyWinDbgSession)

    srv.handle_attach_windbg(arguments={}, timeout=20, verbose=False)
    sess = srv.active_sessions["__windbg_attach__"]

    with sess.shutdown_file.open() as f:
        old_ts = json.load(f)["timestamp"]

    srv.cleanup_sessions()

    with sess.shutdown_file.open() as f:
        new_ts = json.load(f)["timestamp"]

    assert new_ts > old_ts
    assert sess.commands[-1] == "shutdown"
    assert srv.active_sessions == {}


@live_only
def test_cleanup_sessions_live():
    """
    Live variant: we cannot peek at WinDbg internals, so we only verify that
    ``cleanup_sessions`` removes the cache entry and does not raise.
    """
    srv.handle_attach_windbg(arguments={}, timeout=10, verbose=False)
    sess = srv.active_sessions["__windbg_attach__"]

    srv.cleanup_sessions()

    assert "__windbg_attach__" not in srv.active_sessions
    # If the real session exposes `is_connected`, it should now be False.
    if hasattr(sess, "is_connected"):
        assert not sess.is_connected
