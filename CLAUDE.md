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

### Coverage

The code under test runs in **two** processes, and both must be measured or the number lies:

- the hosted server subprocess, where tool dispatch executes. Set `MCP_WINDBG_COVERAGE` so the
  harness launches it under `coverage run --parallel-mode`.
- the pytest process itself, where the hermetic unit tests (`tests/test_*.py`) run. This needs
  `coverage run -m pytest`, not plain `pytest`. Miss this and every unit test reads as dead
  code: that is how `kd_session.py` once reported 34% while fully tested.

Both write parallel-mode data files that `coverage combine` merges:

```powershell
uv run coverage erase
$env:MCP_WINDBG_COVERAGE = "1"
uv run coverage run -m pytest src/mcp_windbg/tests/     # measures pytest AND the server
$env:MCP_WINDBG_COVERAGE = $null
uv run coverage combine                 # merge the per-process .coverage.* files
uv run coverage report                  # or: uv run coverage html  ->  htmlcov/
```

CI runs exactly this and gates on `--fail-under=88`. To reproduce CI's number, leave
`MCP_WINDBG_KERNEL_CONNECTION` unset so the kernel scenarios skip as they do there. Run it with
the variable set for the honest local number, which covers `kd_session.py` against a real
target rather than a fake process.

### Test suite

The suite is a declarative end-to-end harness: each `tests/scenarios/*.yaml` file is run
against a real `python -m mcp_windbg` server hosted over stdio and driven by a real MCP client
(only the LLM is faked, by the scripted tool calls). Scenarios that need a debugger carry the
`live` (and `remote` / `kernel`) marker and `pytest.skip` cleanly when `cdb.exe` or the Git LFS
dump is absent, so `-m "not live"` always runs and stays green off-Windows. See
`src/mcp_windbg/tests/e2e/README.md` for the scenario format. Test dumps live in
`src/mcp_windbg/tests/dumps/` via Git LFS (`git lfs pull`).

**Kernel scenarios.** `kernel_session.yaml` drives a real kernel target through `kd.exe`. CI has
no target machine, so it skips there; locally, point it at a debuggable VM or box and it runs:

```powershell
$env:MCP_WINDBG_KERNEL_CONNECTION = "net:port=50005,key=1.2.3.4"   # the -k connection string
uv run pytest src/mcp_windbg/tests/ -m kernel -v
```

Kernel code paths that CI cannot reach are held up by the hermetic fake-`kd.exe` tests in
`tests/test_kd_session.py`. Treat those as the CI floor, not as proof the feature works: before
shipping a kernel change, run the `kernel` marker against a real target.

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

Releases are driven by [release-please](https://github.com/googleapis/release-please). You do
not bump versions and you do not push tags. You **do** still write `CHANGELOG.md` by hand: it is
configured with `skip-changelog`, so release-please never touches it.

**Branches.** `develop` is the working branch and the base for every `feat/*` and `fix/*` PR;
it is the repository default, so PRs and Dependabot target it automatically. `main` is not a
branch you commit to - `release-please.yml` fast-forwards it onto each released commit, so it
always equals the last released state. Never push to `main` directly: the fast-forward is a
plain one, so a commit on `main` that `develop` lacks fails the next release until someone
merges `main` back into `develop` by hand.

**PR titles decide the version.** Because PRs are squash-merged, the PR title becomes the commit
subject, which is what release-please reads. It must be a Conventional Commit:

| PR title prefix | Bump | Shown in the release notes |
| :-------------- | :--- | :------------------------- |
| `feat:` | minor | yes |
| `fix:` | patch | yes |
| `perf:` `docs:` `deps:` | patch | yes |
| `chore:` `ci:` `test:` `refactor:` `build:` `style:` | patch | hidden |
| any prefix with `!`, or a `BREAKING CHANGE:` footer | **major** | yes |

A title with no recognized prefix contributes nothing. If everything since the last release is
hidden-only (`chore`, `ci`, `test`, ...), release-please opens no release PR at all - by design,
not a failure. `Release-As: X.Y.Z` in a commit body forces an exact version.

**When no release PR appears and you expected one**, read the `release-please` job log. It says
which commits it could use:

```
commit could not be parsed: 30c7dd9 Fix kernel go/break-in handling, add wait_for_break (#80)
Considering: 1 commits
No user facing commits found since c7ae1a6 - skipping
```

`could not be parsed` means the subject was not a Conventional Commit, so it counted for nothing.
`No user facing commits found` means everything that did parse was a hidden type. Neither is an
error; both mean there is nothing to release yet.

**A config change does not rewrite an open release PR.** release-please leaves an existing
release PR alone when the version it computes has not changed, so fixing something that only
affects the PR's *contents* - an `extra-files` entry, say - has no visible effect. Delete the
`release-please--branches--develop--*` branch (which closes the PR) and re-run the workflow
from the Actions tab via `workflow_dispatch`; it rebuilds the PR with the new config.

**Check the release PR's diff before merging it.** It should touch `pyproject.toml`,
`.release-please-manifest.json`, and all three versions in `server.json`. A missing `server.json`
means the `extra-files` entries are not firing.

**The flow.** Every push to `develop` runs `release-please.yml`, which opens or rewrites a
`chore(develop): release X.Y.Z` PR carrying the version bumps. Before merging it, **add the
`CHANGELOG.md` entry to that PR**: rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` to match
the version in the PR title. Merging that PR is what "release now" means. It pushes to `develop`,
which runs the workflow again; this time release-please creates the tag and the GitHub release,
and then, in the same run:

- `release-notes` replaces the generated notes with your CHANGELOG entry.
- `fast-forward-main` pushes `main` to the released commit.
- `publish` calls `publish-mcp.yml` to run the full test matrix and ship to PyPI.
- `docs` calls `pages.yml` to deploy the documentation site.

The last two are chained through `needs` rather than triggered, for the `GITHUB_TOKEN` reason
below: neither the tag nor the `main` fast-forward starts a workflow on its own.

Forgetting the CHANGELOG entry fails the release before it happens. The `preflight` job runs
`scripts/check-version-consistency.ps1` ahead of release-please whenever the head commit is a
`chore(develop): release ...` merge, and release-please does not run unless it passes. It asserts
that `pyproject.toml`, both versions in `server.json`, `.release-please-manifest.json`, the plugin
manifest, the marketplace entry and the `uvx` pin in the plugin's `.mcp.json` all carry the same
version, that `CHANGELOG.md`'s top heading names it, and that the heading has an entry under it.
No tag is created when any of that is wrong, so the fix is a commit to `develop` rather than a
second release.

The same script still runs from the publish build, which is what guards a `publish-mcp.yml`
started by hand.

**What release-please keeps in sync.** `release-please-config.json` drives it. The `python`
release type updates `pyproject.toml`'s `[project] version` natively. `server.json` is not
something it knows about, so it is handled by two `extra-files` JSONPath entries (`$.version` and
`$.packages[*].version`) - the top-level and per-package versions are separate fields, and one
entry carries one JSONPath. The plugin manifest, the marketplace entry and the `uvx` pin in the
plugin's `.mcp.json` are three more `extra-files` entries.

Expect an `extra-files` target to come back **re-serialised**, not patched in place: the updater
parses the file and writes it out again, which is why `plugin.json`'s inline `keywords` array
reflowed to one entry per line on the first release that touched it. Harmless for a small
hand-written manifest; the reason `uv.lock` is not an `extra-files` target.

`uv.lock` is instead regenerated by the `sync-lockfile` job, which runs `uv lock` on the release
PR and commits the result. It records the project's own version, so every bump leaves it a
release behind - which is what `uv lock --check` in the build now fails on. Left to `extra-files`
a 900-line generated lockfile would be re-serialised by a tool that does not own its format.

**Two setup requirements**, easy to lose track of:

- Settings -> Actions -> General -> "Allow GitHub Actions to create and approve pull requests"
  must stay enabled, or opening the release PR fails with a 403.
- Releases created with the default `GITHUB_TOKEN` do not trigger other workflows. That is why
  `publish-mcp.yml` is `workflow_call`/`workflow_dispatch` rather than `on: push: tags` - a tag
  trigger would silently never fire. The same applies to the `main` fast-forward, which is why
  `pages.yml` gained a `workflow_call` trigger instead of watching `main`. It is also why CI does
  not run on the release PR itself; supply a PAT or GitHub App token to `release-please.yml` if
  you want that gate.

## Docs

```powershell
pip install -r requirements-docs.txt
python -m mkdocs serve                  # live preview at http://127.0.0.1:8000
python -m mkdocs build --strict         # what Pages builds; links must resolve
pwsh scripts/Format-Docs.ps1            # markdown typography
```
