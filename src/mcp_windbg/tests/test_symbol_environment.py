"""Automatic dump-directory symbols must not erase the inherited symbol server."""
from __future__ import annotations

import os

import pytest

from mcp_windbg import cdb_session, kd_session
from mcp_windbg.debug_session import DebuggerSession


@pytest.mark.parametrize("module", [cdb_session, kd_session])
@pytest.mark.parametrize("explicit,inherited,automatic", [
    (None, "srv*cache*https://msdl.microsoft.com/download/symbols", True),
    ("explicit", "inherited", True),
    (None, None, True),
    (None, "inherited", False),
    ("explicit", "inherited", False),
])
def test_dump_directory_preserves_the_selected_symbol_path(
    monkeypatch, tmp_path, module, explicit, inherited, automatic
):
    if inherited is None:
        monkeypatch.delenv("_NT_SYMBOL_PATH", raising=False)
    else:
        monkeypatch.setenv("_NT_SYMBOL_PATH", inherited)
    monkeypatch.setattr(module, "find_executable", lambda *args: "fake")
    launches = []
    monkeypatch.setattr(DebuggerSession, "__init__", lambda self, **kwargs: launches.append(kwargs))
    dump = tmp_path / "crash.dmp"
    dump.write_bytes(b"")
    cls = cdb_session.CDBSession if module is cdb_session else kd_session.KDSession
    cls(dump_path=str(dump), symbols_path=explicit, auto_dump_dir_symbols=automatic)
    args = launches[0]["launch_args"]
    if automatic:
        selected = explicit or inherited
        expected = os.path.dirname(os.path.abspath(dump))
        if selected:
            expected += ";" + selected
        assert args[args.index("-y") + 1] == expected
    elif explicit:
        assert args[args.index("-y") + 1] == explicit
    else:
        assert "-y" not in args
