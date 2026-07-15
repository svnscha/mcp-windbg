"""Unit tests for the cdb.exe command-line builder.

These are hermetic (no cdb.exe, no marker) so they run in the ``-m "not live"``
subset. They pin the one thing issues #62 and #47 turn on: a kernel connection
is launched with ``-k``, a user-mode remote with ``-remote``, and a dump with
``-z`` - never mixed up.
"""

from __future__ import annotations

from mcp_windbg.cdb_session import build_cdb_args


def test_dump_uses_z():
    args = build_cdb_args("cdb.exe", dump_path="C:\\dumps\\app.dmp")
    assert args == ["cdb.exe", "-z", "C:\\dumps\\app.dmp"]


def test_user_remote_uses_remote():
    args = build_cdb_args("cdb.exe", remote_connection="tcp:Port=5005,Server=host")
    assert args == ["cdb.exe", "-remote", "tcp:Port=5005,Server=host"]


def test_kernel_net_uses_k():
    args = build_cdb_args("cdb.exe", kernel_connection="net:port=50000,key=1.2.3.4")
    assert args == ["cdb.exe", "-k", "net:port=50000,key=1.2.3.4"]


def test_kernel_named_pipe_uses_k_verbatim():
    conn = r"com:pipe,port=\\.\pipe\com_1,baud=115200,reconnect,resets=0"
    args = build_cdb_args("kd.exe", kernel_connection=conn)
    # The connection string is passed through untouched (no slash rewriting).
    assert args == ["kd.exe", "-k", conn]


def test_kernel_is_not_remote():
    kernel = build_cdb_args("cdb.exe", kernel_connection="net:port=1,key=x")
    remote = build_cdb_args("cdb.exe", remote_connection="net:port=1,key=x")
    assert "-k" in kernel and "-remote" not in kernel
    assert "-remote" in remote and "-k" not in remote


def test_symbols_appended_after_connection():
    args = build_cdb_args(
        "cdb.exe", kernel_connection="net:port=1,key=x", symbols_path="C:\\sym"
    )
    assert args == ["cdb.exe", "-k", "net:port=1,key=x", "-y", "C:\\sym"]


def test_additional_args_appended_last():
    args = build_cdb_args(
        "cdb.exe",
        dump_path="a.dmp",
        symbols_path="S",
        additional_args=["-c", ".reload"],
    )
    assert args == ["cdb.exe", "-z", "a.dmp", "-y", "S", "-c", ".reload"]
