# Changelog

All notable changes to the MCP Server for WinDbg Crash Analysis project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.11.0] - 2025-11-XX

- TBD

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

### 🤝 Community Contributions

Special thanks to [@sooknarine](https://github.com/sooknarine) for these valuable contributions:
- [Find local dumps with other common extensions #6](https://github.com/svnscha/mcp-windbg/pull/6) - Now finds more crash dump files automatically
- [Add support for remote debugging #10](https://github.com/svnscha/mcp-windbg/pull/10) - Connect to live debugging sessions


## [0.1.0] - 2025-05-03

- Initial version as blogged about.
