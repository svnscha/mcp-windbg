"""End-to-end test framework for the mcp-windbg MCP server.

Scenarios are declarative YAML files under ``tests/scenarios``; the runner hosts
a real ``python -m mcp_windbg`` server process and drives it as a real MCP
client, replacing only the LLM. See ``README.md`` in this directory.
"""
