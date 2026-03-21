from __future__ import annotations

import sys

import mcp_windbg


def test_main_passes_filter_script_to_stdio(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_serve(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mcp_windbg, "serve", fake_serve)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mcp-windbg", "--filter-script", "D:/filters/redact.py", "--transport", "stdio"],
    )

    mcp_windbg.main()

    assert captured["filter_script"] == "D:/filters/redact.py"


def test_main_passes_filter_script_to_http(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_serve_http(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mcp_windbg, "serve_http", fake_serve_http)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mcp-windbg",
            "--transport",
            "streamable-http",
            "--filter-script",
            "D:/filters/redact.py",
        ],
    )

    mcp_windbg.main()

    assert captured["filter_script"] == "D:/filters/redact.py"
