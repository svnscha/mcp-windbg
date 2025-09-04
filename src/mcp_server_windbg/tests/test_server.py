"""
Tests for mcp_server_windbg.server

These tests focus on logic that doesn’t require a running CDB session:
* session caching / re-use (`get_or_create_session`)
* unloading sessions (`unload_session`)
* the helper that runs the four “common” WinDBG commands
  (`execute_common_analysis_commands`)
"""

from pathlib import Path
import importlib
import pytest

import mcp_server_windbg.server as srv


# --------------------------------------------------------------------------- #
#                             test utilities                                  #
# --------------------------------------------------------------------------- #
class DummySession:
    """Light-weight stand-in for CDBSession."""

    def __init__(self, dump_path, *_, **__):
        self.dump_path = Path(dump_path)
        self.commands = []

    # the server helper calls this
    def send_command(self, cmd):
        self.commands.append(cmd)
        return [f"dummy-output for {cmd}"]

    # unload_session expects this to exist
    def shutdown(self):
        self.commands.append("shutdown")


@pytest.fixture(autouse=True)
def _clean_active_sessions():
    """Make sure global cache is empty before / after every test."""
    yield
    srv.active_sessions.clear()


@pytest.fixture
def fake_dump(tmp_path):
    """Create a tiny placeholder *.dmp file on disk."""
    dmp = tmp_path / "fake.dmp"
    dmp.write_text("dummy")
    return dmp


# --------------------------------------------------------------------------- #
#                                 tests                                       #
# --------------------------------------------------------------------------- #
def test_get_or_create_caches(monkeypatch, fake_dump):
    """Same dump path should return the *same* DummySession object."""
    # Patch CDBSession → DummySession
    monkeypatch.setattr(srv, "CDBSession", DummySession)

    one = srv.get_or_create_session(str(fake_dump))
    two = srv.get_or_create_session(str(fake_dump))

    # same instance back?
    assert one is two
    # path stored in cache?
    assert str(fake_dump.resolve()) in srv.active_sessions  #  get_or_create_session logic

    # sanity
    assert isinstance(one, DummySession)


def test_unload_session(monkeypatch, fake_dump):
    """unload_session should call shutdown() and purge the cache entry."""
    monkeypatch.setattr(srv, "CDBSession", DummySession)
    session = srv.get_or_create_session(str(fake_dump))

    ok = srv.unload_session(str(fake_dump))  # unload_session logic
    assert ok is True
    # entry removed
    assert str(fake_dump.resolve()) not in srv.active_sessions
    # our dummy recorded the shutdown
    assert "shutdown" in session.commands


def test_execute_common_analysis_commands(monkeypatch, fake_dump):
    """Helper should issue the four expected WinDBG commands in order."""
    monkeypatch.setattr(srv, "CDBSession", DummySession)
    session = srv.get_or_create_session(str(fake_dump))

    results = srv.execute_common_analysis_commands(session)  # helper logic
    # returned dict has exactly the expected keys
    assert set(results) == {"info", "exception", "modules", "threads"}
    # commands actually called on the DummySession, in the right order
    assert session.commands == [".lastevent", "!analyze -v", "lm", "~"]
