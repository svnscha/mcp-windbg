"""Remote transcript echoes and foreign clients cannot complete our commands."""
from __future__ import annotations

import threading

import pytest

from mcp_windbg.debug_session import DebuggerSession, MARKER_BASE
from test_debug_session import make_session, _single_byte_code_page


def test_different_sessions_never_share_a_marker(make_session):
    first, _ = make_session()
    second, _ = make_session()
    assert first._marker_seq == second._marker_seq
    assert first._next_marker() != second._next_marker()


@pytest.mark.parametrize("kind", ["echo", "prefix", "foreign", "target_text"])
def test_only_exact_standalone_marker_completes_command(make_session, kind):
    session, proc = make_session()
    original_handle = proc._handle
    seen = threading.Event()
    markers = []
    candidate_lines = []
    outcome = []

    def on_line(line):
        if line in candidate_lines:
            seen.set()

    def handle(line):
        if not line.startswith(".echo "):
            return original_handle(line)
        marker = line.removeprefix(".echo ")
        markers.append(marker)
        if kind == "echo":
            candidate = f"[server (tcp port:5005)] 0:000> .echo {marker}"
        elif kind == "prefix":
            candidate = marker + "0"
        elif kind == "foreign":
            candidate = f"{MARKER_BASE}_{'0' * 32}_{session._marker_seq}"
        else:
            candidate = f"application mentions {marker}, not a completion"
        candidate_lines.append(candidate)
        proc._out.put(candidate)

    def command():
        try:
            outcome.append(session.send_command("r", timeout=2))
        except Exception as error:
            outcome.append(error)

    proc._handle = handle
    session._on_output_line = on_line
    worker = threading.Thread(target=command)
    worker.start()
    try:
        assert seen.wait(1)
        assert not session.ready_event.wait(0.05)
        assert not outcome
    finally:
        if markers:
            proc._out.put(markers[0])
        worker.join(timeout=3)
        proc._handle = original_handle
    assert not worker.is_alive()
    expected = ["OUT:r"]
    if kind == "target_text":
        expected += candidate_lines
    assert outcome == [expected]


def test_unicode_log_uses_complete_lines_not_marker_substrings(tmp_path):
    session = DebuggerSession.__new__(DebuggerSession)
    marker = f"{MARKER_BASE}_{'a' * 32}_2"
    content = (
        f"application mentions {marker}\r\n"
        f"0:000> .echo {marker}0\r\n{marker}0\r\n"
        f"0:000> .echo {marker}\r\n{marker}\r\n"
    )
    path = tmp_path / "debugger.log"
    path.write_bytes(content.encode("utf-16-le"))
    session._log_path = str(path)
    session._log_offset = 0
    session.timeout = 1
    assert session._read_log_segment(marker) == [
        f"application mentions {marker}", marker + "0"
    ]
    assert session._log_offset == len(content.encode("utf-16-le"))
