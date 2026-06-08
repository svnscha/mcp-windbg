"""Pytest discovery for the declarative end-to-end scenarios.

Every ``scenarios/*.yaml`` file becomes one parametrized test case, tagged with
the ``live`` / ``remote`` markers its requirements imply so the hermetic subset
can be selected with ``-m "not live"``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the ``e2e`` support package importable (this dir is on sys.path under
# pytest, but be explicit so direct invocation works too). ``tests`` is
# deliberately not a Python package, so the dumps never ship in the wheel.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e.harness import SCENARIOS_DIR  # noqa: E402
from e2e.runner import load_scenario, scenario_markers  # noqa: E402


def pytest_generate_tests(metafunc):
    if "scenario" not in metafunc.fixturenames:
        return

    params = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        scenario = load_scenario(path)
        marks = [getattr(pytest.mark, name) for name in sorted(scenario_markers(scenario))]
        params.append(pytest.param(scenario, id=scenario["name"], marks=marks))

    metafunc.parametrize("scenario", params)
