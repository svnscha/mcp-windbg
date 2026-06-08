"""Entry point: run each discovered YAML scenario against a hosted MCP server.

Scenarios are parametrized in ``conftest.py``; each is executed end-to-end here.
The whole framework lives in ``e2e/`` and the cases in ``scenarios/*.yaml`` -
this file only bridges pytest to the async runner.
"""

from __future__ import annotations

import anyio

from e2e.runner import run_scenario


def test_scenario(scenario):
    anyio.run(run_scenario, scenario)
