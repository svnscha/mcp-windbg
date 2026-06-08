# mcp-windbg

A [Model Context Protocol](https://modelcontextprotocol.io/) server that lets AI models
analyze Windows crash dumps and drive live or remote debugging through WinDbg/CDB. It is a
Python wrapper around `cdb.exe`: the model calls MCP tools, the server runs the matching
debugger commands and returns the text output. Windows-only (needs CDB). The single entry
point is `mcp-windbg`, which speaks MCP over stdio or streamable-http.

## Build / test / run

Python 3.10+ with the `uv` package manager. CDB must be installed for the live tests (WinDbg
from the Microsoft Store, or the Windows SDK).

```powershell
uv sync --dev                                                       # install incl. dev deps
uv run pytest src/mcp_windbg/tests/ -v                             # full test suite
uv run pytest src/mcp_windbg/tests/ -v -m "not live"              # hermetic subset (no CDB)
uv run python -m mcp_windbg --verbose                              # run the server (stdio)
uv run python -m mcp_windbg --transport streamable-http --port 8000   # HTTP transport
```

Coverage measures the hosted server subprocess (the parent pytest process barely touches the
server code, so plain `--cov` is misleading). Set `MCP_WINDBG_COVERAGE` so the harness launches
the server under `coverage run --parallel-mode`, then combine and report:

```powershell
uv run coverage erase
$env:MCP_WINDBG_COVERAGE = "1"; uv run pytest src/mcp_windbg/tests/; $env:MCP_WINDBG_COVERAGE = $null
uv run coverage combine                 # merge the per-subprocess .coverage.* files
uv run coverage report                  # or: uv run coverage html  ->  htmlcov/
```

The suite is a declarative end-to-end harness: each `tests/scenarios/*.yaml` file is run
against a real `python -m mcp_windbg` server hosted over stdio and driven by a real MCP client
(only the LLM is faked, by the scripted tool calls). Scenarios that need a debugger carry the
`live` (and `remote`) marker and `pytest.skip` cleanly when `cdb.exe` or the Git LFS dump is
absent, so `-m "not live"` always runs and stays green off-Windows. See
`src/mcp_windbg/tests/e2e/README.md` for the scenario format. Test dumps live in
`src/mcp_windbg/tests/dumps/` via Git LFS (`git lfs pull`).

## Layout

```
src/mcp_windbg/
  __init__.py        main(): CLI argument parsing, picks the transport
  __main__.py        module entry point
  server.py          MCP server: tool param models + list_tools + call_tool dispatch
  cdb_session.py     CDBSession: spawns cdb.exe, sends commands, reads output
  filter_script.py   --filter-script loader and tool content hooks
  prompts/           prompt templates (dump-triage.prompt.md)
  tests/             e2e harness: e2e/ (runner + harness), scenarios/*.yaml, dumps/ (Git LFS)
scripts/             check-version-consistency.ps1, validate-server-schema.py, Format-Docs.ps1
examples/            small C++ programs that crash, for generating test dumps
docs/                MkDocs user guide (Material), deployed to GitHub Pages
.github/workflows/   ci.yml -> build-and-test.yml (tests), publish-mcp.yml (PyPI on v* tags),
                     pages.yml (docs deploy)
pyproject.toml       project + dependency config         server.json   MCP registry manifest
```

## Conventions

Topic-scoped conventions live in `.claude/rules/` and load automatically when you read a
matching file:

- `markdown.md` - Markdown typography for every `*.md`: plain hyphens (no em/en dashes), no
  emojis. Run `pwsh scripts/Format-Docs.ps1`. (`**/*.md`)
- `documentation.md` - authoring style for the `docs/` user guide (scenario-first, link to
  the reference, sentence-case). (`docs/**`)

Tool and CLI facts come from `src/mcp_windbg/server.py` (tool schemas) and
`src/mcp_windbg/__init__.py` (command-line options). Keep `docs/reference/` in sync with them.

## Versioning and release

The version lives in three places that must agree: `pyproject.toml` (`version`), `server.json`
(top-level `version` and every `packages[*].version`), and the top `## [x.y.z]` heading in
`CHANGELOG.md`. Day-to-day work lands under a `## [Unreleased]` heading; the version-
consistency check (`scripts/check-version-consistency.ps1`) only runs on release builds, so
`main` may sit at `[Unreleased]`.

To release: bump all three to the new version, set the CHANGELOG date, then tag `vX.Y.Z` and
push. `publish-mcp.yml` runs the tests, builds, publishes to PyPI, and creates the GitHub
release; that workflow runs the consistency check (it calls build-and-test with
`check-version: true`).

## Docs

```powershell
pip install -r requirements-docs.txt
python -m mkdocs serve                  # live preview at http://127.0.0.1:8000
python -m mkdocs build --strict         # what Pages builds; links must resolve
pwsh scripts/Format-Docs.ps1            # markdown typography
```
