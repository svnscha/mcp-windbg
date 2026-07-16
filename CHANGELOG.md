# Changelog

All notable changes to the MCP Server for WinDbg Crash Analysis project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-16

First stable release, and the one where kernel debugging actually works.

The tool surface is redesigned around opaque **session ids** and split by debugger engine:
user-mode (`cdb.exe`) and kernel (`kd.exe`). **This is a breaking change** for clients of the
0.x tools. Every `*_windbg_*` tool has a new name, listed under Changed.

### Added

- **Kernel debugging** - `open_kd_session`, `run_kd_command`, and `close_kd_session` attach to a
  kernel target over KDNET (`net:port=,key=`), a named pipe (`com:pipe,port=\\.\pipe\...`), or
  serial, driven by `kd.exe`. This was impossible in 0.x: every remote connection was launched
  with `-remote`, which is user-mode only, so a kernel target could never connect (#62, #47).
- **Kernel sessions arrive already stopped** - `open_kd_session` waits for the target's connect
  banner and breaks in for you, so there is no separate break-in step.
- **Closing a kernel session sets the machine running again** - `close_kd_session` sends `g` by
  default (`resume: true`). Pass `resume: false` only to leave the target halted on purpose.
- **`--kd-path`** - point the server at a specific `kd.exe`, the counterpart to `--cdb-path`.
  Kernel sessions need their own option because `cdb.exe` cannot drive a kernel cable.
- **Session ids** - every `open_*` returns an opaque `session_id` (`cdb-…` / `kd-…`) on its first
  output line, and `run_*`, `close_*`, and `send_ctrl_break` address a session by that id. The
  kind is enforced: a `cdb` id passed to `run_kd_command` (or the reverse) returns an error
  naming the right tool.
- **Per-call timeouts** - `timeout_seconds` on any `open_*` / `run_*` overrides that tool's
  default (`open_cdb_dump` 180s, connects 60s, `run_cdb_command` 60s, `run_kd_command` 120s).
  The server-wide `--timeout` (default 60s) is the floor.
- **`remote-triage` prompt** - a guided investigation of a live user-mode target: break in,
  orient, then track down the hang or crash.
- **`kernel-triage` prompt** - the same for a kernel target, including telling a real bugcheck
  apart from a plain break-in, and releasing the machine at the end.

### Changed

- **BREAKING** - `list_windbg_dumps` is now `list_dumps`.
- **BREAKING** - `open_windbg_dump` is now `open_cdb_dump`.
- **BREAKING** - `open_windbg_remote` is now `open_cdb_remote`.
- **BREAKING** - `run_windbg_cmd` is now `run_cdb_command` (user mode) or `run_kd_command`
  (kernel). The `connection_type` discriminator is gone; the tool name says which engine.
- **BREAKING** - `close_windbg_dump` and `close_windbg_remote` are both now `close_cdb_session`.
- **BREAKING** - `send_ctrl_break` takes a `session_id`.
- **BREAKING** - sessions are addressed by id, not by dump path or connection string, and `run_*`
  no longer opens one implicitly: open a session first. Reopening the same dump gives you an
  independent session instead of silently reusing one.
- **Module layout** - kernel sessions live in `kd_session.py` and user-mode in `cdb_session.py`,
  over a shared `debug_session.py` that owns the subprocess and marker protocol.
- **Docs** - README and the docs site rewritten around the new tools and the session-id flow,
  with a "Debug a kernel target" guide, a Development page, and client guides for Claude Code and
  Autohand Code (#63). Setup instructions standardize on `pip install mcp-windbg` and
  `python -m mcp_windbg`.
- **Dependencies** - runtime floors refreshed (`mcp>=1.28.1`, `pydantic>=2.13.4`,
  `starlette>=1.3.1`, `uvicorn>=0.51.0`), plus `pytest>=9.1.1`, and `uv.lock` regenerated. The
  streamable-http transport was smoke-tested against the Starlette 0.x to 1.x bump.

### Fixed

- **A slow command no longer wedges a live session.** When a command outruns its timeout the
  debugger is still busy running it, so the server now breaks in with CTRL+BREAK and
  resynchronizes before reporting the timeout. The session is usable again for the next command
  instead of stranded.
- **A slow command's output is no longer attributed to the next command.** Every command waits
  on its own unique completion marker, so late output from one command can never be mistaken for
  the next one's result.
- **Debugger processes are cleaned up on close.** `cdb.exe` launched through the Microsoft Store
  execution aliases spawns a child that a plain terminate left behind, still holding the target.
  Shutdown now does a Windows process-tree kill.

### Removed

- All `*_windbg_*` tool names, and the `connection_type` parameter on `run_windbg_cmd` and
  `send_ctrl_break`, superseded by the `cdb` / `kd` split.
- Session reuse by dump path or connection string. `open_*` always creates a fresh session and
  returns its id.

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
