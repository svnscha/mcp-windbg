"""Error responses must survive the tool-definition extraction."""

import asyncio

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import GetPromptRequestParams, INTERNAL_ERROR

from mcp_windbg import server_support


def test_missing_prompt_reports_original_mcp_error(monkeypatch):
    def missing_prompt(name):
        raise FileNotFoundError("test missing prompt")

    monkeypatch.setattr(server_support, "load_prompt", missing_prompt)
    with pytest.raises(MCPError, match="Prompt file not found") as exc:
        asyncio.run(server_support.on_get_prompt(None, GetPromptRequestParams(name="dump-triage")))
    assert exc.value.code == INTERNAL_ERROR
