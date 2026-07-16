# Changelog

All notable changes to the MCP Server for WinDbg Crash Analysis project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`--kd-path`**: point the server at a specific `kd.exe` for kernel debugging, the counterpart
  to `--cdb-path`. Kernel sessions need `kd.exe` (`cdb.exe` cannot drive a kernel connection),
  so the path was previously always auto-detected with no way to override it.
- **Real kernel end-to-end test**: `scenarios/kernel_session.yaml` attaches to a live kernel
  target, runs kernel-mode commands, and releases it on close. Set `MCP_WINDBG_KERNEL_CONNECTION`
  to the `-k` connection string to run it (`-m kernel`); it skips where no target is configured.

### Fixed

- **Coverage measurement**: CI ran the test suite under plain `pytest`, so only the hosted server
  subprocess was measured and every hermetic unit test counted as zero. Fully tested modules
  reported as near-dead code (`kd_session.py` at 34%). CI now runs `coverage run -m pytest`, which
  measures the pytest process and the server subprocess together.

## [1.0.0] - 2026-07-16

First stable release. The tool surface is redesigned around opaque **session ids** and split
by debugger engine - user-mode (`cdb.exe`) and kernel (`kd.exe`). **This is a breaking change**
for clients of the 0.x tools; see the renames below.

### Added

- **Kernel debugging (`kd.exe`)**: `open_kd_session` / `run_kd_command` / `close_kd_session`
  attach to a kernel target with `-k` (KDNET `net:port=,key=`, named pipe `com:pipe,port=\\.\pipe\...`,
  or serial) using `kd.exe`. On connect the server waits for the target's connect banner, breaks
  in with CTRL+BREAK, and returns an already-stopped session. Kernel debugging was previously
  impossible - remote connections were launched with `-remote` (user-mode only), so a kernel
  target could never connect (#62, #47).
- **Session-id model**: every `open_*` tool returns an opaque `session_id` (`cdb-…` / `kd-…`) on
  the first output line; `run_*`, `close_*`, and `send_ctrl_break` address a session by that id.
  Session kind is enforced - using a `cdb` id with `run_kd_command` (or vice versa) returns a
  tool error naming the correct tool.
- **Per-tool-call timeouts**: `timeout_seconds` on every `open_*`/`run_*` overrides the per-tool
  default (`open_cdb_dump` 180s, connects 60s, `run_cdb_command` 60s, `run_kd_command` 120s);
  the server-wide `--timeout` (now defaulting to 60s) is a floor.
- **Cancel-on-timeout for live sessions**: a remote/kernel command that outruns its timeout is
  broken into with CTRL+BREAK and the session is resynchronized before the timeout is reported,
  so a slow command (e.g. `!process 0 0` over a flaky KDNET link) can no longer wedge the session.
- **Resume-on-close for kernel sessions**: `close_kd_session` now resumes the target machine by
  default (`resume: true`), sending `g` so it runs again instead of leaving the whole machine
  frozen at the break. `resume: false` opts out. (CTRL+B, which detaches a user-mode remote,
  does not resume a kernel target.)
- **Module split**: kernel sessions live in a new `kd_session.py`, user-mode in `cdb_session.py`,
  both over a shared `debug_session.py` base that owns the subprocess and completion-marker
  protocol. Adds a "Debug a kernel target" guide and an LLM-executable feature-verification plan.

### Changed

- **BREAKING - tool surface renamed and split** (all `*_windbg_*` names removed):
    - `list_windbg_dumps` → `list_dumps`
    - `open_windbg_dump` → `open_cdb_dump`
    - `open_windbg_remote` → `open_cdb_remote`
    - `run_windbg_cmd` → `run_cdb_command` (user mode) / `run_kd_command` (kernel); the
      `connection_type` discriminator is gone
    - `close_windbg_dump` / `close_windbg_remote` → `close_cdb_session`; new `open_kd_session` /
      `close_kd_session`
    - `send_ctrl_break` now takes a `session_id`
- **BREAKING - session addressing**: sessions are addressed by `session_id`, not by dump path /
  connection string, and `run_*` no longer auto-creates a session (open one first). Reopening the
  same dump now yields an independent session rather than reusing one.
- **Completion markers are unique per command**, so a slow command's late output can never be
  attributed to the next command (fixes cross-command desync on live targets).
- **Kernel debugging uses `kd.exe`** (auto-detected), not `cdb.exe -k`.
- **Docs**: README, the docs site (tools/CLI reference, crash-dump/remote/kernel/triage guides,
  troubleshooting), and the `dump-triage` prompt updated to the new tools and session-id flow;
  `TESTPLAN.md` rewritten as an LLM-executable protocol.
- **Docs**: Added a Claude Code client guide (`claude mcp add` with both `uvx` and `python -m mcp_windbg`)
  and documented Autohand Code (`autohand mcp add --scope project`) in `docs/reference/clients.md`
  (#63); added a Development page (`docs/development.md`); switched the README Star History chart to
  the sealed-token embed with light/dark variants.
- **Dependencies**: Refreshed the runtime floors - `mcp>=1.28.1`, `pydantic>=2.13.4`,
  `starlette>=1.3.1` (0.x to 1.x), `uvicorn>=0.51.0` - and `pytest>=9.1.1`, and regenerated
  `uv.lock`. The streamable-http transport was smoke-tested against the Starlette 1.x bump.

### Fixed

- **Kernel targets connect reliably** via `kd.exe` with a proper connect → break-in handshake,
  instead of hanging on a `-k` connection that `cdb.exe` could not drive (#62, #47).
- **Slow live commands no longer hang the session**: they time out cleanly and the session
  remains usable for the next command.
- **Closing a kernel session no longer freezes the machine**: it now resumes the target with
  `g` (see resume-on-close above) instead of a CTRL+B that left the whole machine halted.
- **Debugger processes are cleaned up on close**: `cdb.exe`/`kd.exe` launched via the Microsoft
  Store execution aliases spawn a child that a plain terminate left behind holding the target;
  shutdown now does a Windows process-tree kill so no stray debugger keeps the connection.

### Removed

- All `*_windbg_*` tool names and the `run_windbg_cmd` / `send_ctrl_break` `connection_type`
  parameter, superseded by the `cdb`/`kd` split.
- Path / connection-string session reuse - `open_*` always creates a fresh session and returns
  its id.

## [0.15.0] - 2026-06-08

### Added

- **Documentation site**: New MkDocs (Material) user guide under `docs/`, deployed to GitHub Pages via `pages.yml`. Covers getting started, the use cases, and a reference for the command-line options, tools, and client configuration. Content migrated and trimmed from the project wiki.
- **Prompts reference**: Documented the built-in `dump-triage` MCP prompt and its `dump_path` argument (`docs/reference/prompts.md`).
- **Usage guide coverage**: New use-case pages for running the server over HTTP (`Debug from another machine`) and scrubbing tool output (`Redact sensitive data`), plus WER auto-capture setup in the triage guide and the `dump-triage` prompt in the crash-dump guide. Documented the HTTP transport's lack of authentication, that attach-by-PID is unsupported, and that sessions are concurrent.

### Changed

- **Package metadata**: Filled in distribution metadata so `pip show` / PyPI are complete - added the author/maintainer email, project URLs (Homepage, Repository, Issues, Changelog), and classifiers for Windows, console environment, and the Debuggers/QA topics (#36)
- **Contributor guide**: Migrated `AGENTS.md` to `CLAUDE.md` and added `.claude/rules/` (Markdown typography and documentation authoring), plus `scripts/Format-Docs.ps1` to enforce the typography rules
- **Docs tooling**: Bumped the docs build dependencies in `requirements-docs.txt` to their latest patch floors - `mkdocs>=1.6.1`, `mkdocs-material>=9.7.6`, `pymdown-extensions>=10.21.3` (consolidates #51, #52, #53)
- **Test suite**: Replaced the ad-hoc tests with a declarative end-to-end harness. Each `tests/scenarios/*.yaml` runs against a really-hosted `python -m mcp_windbg` server driven by a real MCP client (only the LLM is faked). Live scenarios carry `live`/`remote` markers and skip cleanly without CDB, so `-m "not live"` runs anywhere. Adds `pyyaml` as a dev dependency.
- **Coverage**: The harness hosts the server under `coverage run --parallel-mode` (set `MCP_WINDBG_COVERAGE`), so coverage reflects the subprocess where tool dispatch runs rather than the test process. The suite reaches 90%+; CI enforces a floor with `coverage report --fail-under=88` (margin below the ~91% actual for cross-version and cdb-output jitter). Code that cannot be line-measured end-to-end (the streamable-http transport on Windows, atexit cleanup, debug-only and defensive branches) is excluded with documented `# pragma: no cover` and `exclude_also` rules. Adds `coverage` as a dev dependency.
- **LFS dumps are mandatory**: a scenario whose `requires.dump` file is missing now hard-fails (run `git lfs pull`) instead of skipping, so a half-set-up checkout is a loud error rather than silent green.

### Removed

- **Dead code**: Removed three unused functions surfaced while raising test coverage - `server.execute_common_analysis_commands`, `CDBSession.get_session_id`, and `prompts.get_available_prompts`. None had callers.

### Fixed

- **Stdio server resilience**: The stdio transport no longer crashes on a malformed input line. `serve()` now uses the SDK default `raise_exceptions=False`, so an unparseable line (e.g. when the server is run directly in a terminal) is logged instead of tearing down the whole process (#45)

## [0.14.0] - 2026-03-21

### Added

- **Tool Content Filter Script Hooks**: Added `--filter-script` so trusted Python helpers can rewrite string-valued tool arguments and tool text output for use cases like PII redaction without exposing full MCP protocol messages

## [0.13.0] - 2026-03-18

### Added

- **Live Debugger Break-In**: Added the `send_ctrl_break` tool to interrupt an active CDB/WinDbg session with CTRL+BREAK for dump and remote debugging workflows (#40)

### Changed

- **Dependency Refresh**: Updated runtime dependency floors for `mcp`, `pydantic`, `starlette`, and `uvicorn`, and refreshed test and validation tooling versions in `pyproject.toml`
- **CI Dependency Maintenance**: Updated GitHub Actions dependencies for Python setup and artifact handling in release workflows (#39, #42)

### Fixed

- **Registry Compatibility**: Restored MCP registry compatibility by reverting `server.json` to the supported `2025-10-17` schema version
- **Publishing Workflow**: Adjusted MCP publishing workflows to match current registry publisher behavior

## [0.12.2] - 2025-12-15

### Fixed

- **Registry Schema Migration**: Updated MCP server schema from deprecated `2025-10-17` to current `2025-12-11` version for mcp-publisher compatibility

## [0.12.1] - 2025-12-15

### Added

- **HTTP Transport in Registry**: Added `streamable-http` transport configuration to server.json for MCP registry discovery
- **Schema Validation in CI**: New `validate-server-schema.py` script validates server.json against the official MCP schema

### Fixed

- **Registry Schema Update**: Updated MCP server schema version from 2025-09-29 to 2025-10-17 for compatibility with registry.modelcontextprotocol.io
- **CI Cache Warning**: Disabled unnecessary dependency caching in PyPI publish job to eliminate spurious warnings

## [0.12.0] - 2025-12-15

### Added

- **HTTP Transport Support**: New `--transport streamable-http` option enables HTTP-based communication alongside the default stdio transport (#31)
- **MCP Prompt API**: Implemented prompt templates for AI-assisted crash dump triage and analysis (#25)

### Changed

- **Updated Dependencies**: Bumped `mcp` to 1.17.0, `pydantic` to 2.12.0, and other dependencies (#26)
- **Improved Prompt Templates**: Removed hard-coded model references from prompt templates for better flexibility (#29)
- **Updated Dependabot Configuration**: Improved automated dependency update settings

### Fixed

- **Session Cleanup**: Prevent stale debugging sessions if `.shutdown()` fails (#28)

## [0.10.0] - 2025-10-10

**What's New in This Release**

This release focuses on making mcp-windbg more reliable, faster, and easier to use for everyone - from beginners to advanced users.

### New Features

**Core**
- Live debugging session support via `open_windbg_remote` and `close_windbg_remote`
- Extended dump file support for `.mdmp` and `.hdmp` formats
- Microsoft Store WinDbg CDB compatibility

**Devops**
- Set up continuous integration that automatically tests the code with Python versions 3.10 through 3.14
- Added automatic dependency updates to keep everything secure and up-to-date
- Streamlined the release process so new versions reach users faster

**Development**
- Switched to `uv` - a lightning-fast Python package manager that's 10-100x faster than pip
- Development setup is now much quicker with commands like `uv sync` and `uv run`
- More reliable builds with locked dependency versions

**Documentation**
- Added comprehensive debugging instructions for AI assistants ([`AGENTS.md`](AGENTS.md))
- Created structured templates to help analyze crash dumps more effectively ([`.github/prompts/dump-triage.prompt.md`](.github/prompts/dump-triage.prompt.md))
- All documentation is now available in the [repository Wiki](https://github.com/svnscha/mcp-windbg/wiki) for easy access
- Simplified the main [`README.md`](README.md) to focus on getting started quickly
- Added this structured [`CHANGELOG.md`](CHANGELOG.md) to track all project changes

### Improvements

**Performance Boost**: Build times are significantly faster thanks to the new tooling
**Enhanced Security**: Automatic scanning and updates keep dependencies secure

### Community Contributions

Special thanks to [@sooknarine](https://github.com/sooknarine) for these valuable contributions:
- [Find local dumps with other common extensions #6](https://github.com/svnscha/mcp-windbg/pull/6) - Now finds more crash dump files automatically
- [Add support for remote debugging #10](https://github.com/svnscha/mcp-windbg/pull/10) - Connect to live debugging sessions


## [0.1.0] - 2025-05-03

- Initial version as blogged about.
