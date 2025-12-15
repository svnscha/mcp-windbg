# mcp-windbg Development Guide

This document describes the repository structure, development workflows, and release process for the mcp-windbg project.

## Repository Structure

```
mcp-windbg/
├── src/mcp_windbg/           # Main source code
│   ├── __init__.py           # Entry point, CLI argument parsing
│   ├── __main__.py           # Module entry point
│   ├── server.py             # MCP server implementation
│   ├── cdb_session.py        # CDB/WinDbg session management
│   ├── prompts/              # Prompt templates for AI assistants
│   │   └── dump-triage.prompt.md
│   └── tests/                # Test suite
│       ├── test_cdb.py       # Core CDB functionality tests
│       ├── test_remote_debugging.py  # Remote debugging tests
│       └── dumps/            # Test crash dump files (Git LFS)
├── scripts/                  # Utility scripts
│   └── check-version-consistency.ps1  # Validates version sync
├── examples/                 # Example crash programs (C++)
│   ├── build.ps1             # Build script for examples
│   └── *.cpp                 # Various crash scenarios
├── .github/
│   ├── workflows/            # CI/CD pipelines
│   │   ├── ci.yml            # Main CI entry point
│   │   ├── build-and-test.yml # Build and test workflow
│   │   └── publish-mcp.yml   # PyPI publishing workflow
│   ├── dependabot.yml        # Automated dependency updates
│   └── prompts/              # GitHub Copilot prompt files
├── pyproject.toml            # Python project configuration
├── server.json               # MCP server manifest
├── CHANGELOG.md              # Version history
└── README.md                 # User documentation
```

## Version Management

**Critical**: Version numbers must be synchronized across three files:

| File | Location |
|------|----------|
| [pyproject.toml](pyproject.toml) | `version = "X.Y.Z"` |
| [server.json](server.json) | `"version": "X.Y.Z"` and `"packages[0].version": "X.Y.Z"` |
| [CHANGELOG.md](CHANGELOG.md) | `## [X.Y.Z] - YYYY-MM-DD` |

Run the version consistency check before committing:
```powershell
.\scripts\check-version-consistency.ps1
```

Run the server.json schema validation:
```powershell
uv run python scripts/validate-server-schema.py
```

Both checks also run automatically in CI.

## Making a New Release

### 1. Update Version Numbers

Update all three files with the new version:

```powershell
# Example: Updating to version 0.13.0
# Edit pyproject.toml: version = "0.13.0"
# Edit server.json: "version": "0.13.0" (both places)
# Edit CHANGELOG.md: ## [0.13.0] - 2025-XX-XX
```

### 2. Update CHANGELOG.md

Follow [Keep a Changelog](https://keepachangelog.com/) format:

```markdown
## [0.13.0] - 2025-01-15

### Added
- New feature description

### Changed
- Modified behavior description

### Fixed
- Bug fix description
```

### 3. Verify and Commit

```powershell
# Verify versions are in sync
.\scripts\check-version-consistency.ps1

# Run tests locally
uv run pytest src/mcp_windbg/tests/ -v

# Commit changes
git add pyproject.toml server.json CHANGELOG.md
git commit -m "chore: bump version to 0.13.0"
```

### 4. Create Release Tag

```powershell
git tag v0.13.0
git push origin main
git push origin v0.13.0
```

The `publish-mcp.yml` workflow triggers on `v*` tags and:
- Runs full test suite
- Builds the package
- Publishes to PyPI
- Creates a GitHub Release

## Running Tests

### Prerequisites

- Windows with WinDbg/CDB installed (via Microsoft Store or SDK)
- Python 3.10+ with `uv` package manager

### Install Development Dependencies

```powershell
uv sync --dev
```

### Run All Tests

```powershell
uv run pytest src/mcp_windbg/tests/ -v
```

### Run Specific Test Files

```powershell
# Core CDB tests
uv run pytest src/mcp_windbg/tests/test_cdb.py -v

# Remote debugging tests
uv run pytest src/mcp_windbg/tests/test_remote_debugging.py -v
```

### Test Requirements

- Tests require a working CDB installation (auto-detected from common paths)
- Test dump files are stored in `src/mcp_windbg/tests/dumps/` via Git LFS
- Remote debugging tests may take longer due to server setup/teardown

## Development Workflow

### Local Development

```powershell
# Install in development mode
uv sync --dev

# Run the server locally (stdio mode)
uv run python -m mcp_windbg --verbose

# Run with HTTP transport
uv run python -m mcp_windbg --transport streamable-http --port 8000
```

### Code Quality

The project uses:
- `pytest` for testing
- Type hints throughout the codebase
- Pydantic for data validation

### Adding New Features

1. Create/modify code in `src/mcp_windbg/`
2. Add corresponding tests in `src/mcp_windbg/tests/`
3. Update CHANGELOG.md with the new feature
4. Run tests locally before pushing

## CI/CD Pipeline

### Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Push/PR to main | Runs build-and-test |
| `build-and-test.yml` | Called by other workflows | Tests on Python 3.10-3.14 |
| `publish-mcp.yml` | Tag `v*` | Publishes to PyPI |

### CI Steps

1. Install WinDbg from Microsoft Store
2. Set up Python via `uv`
3. Install dependencies
4. Run pytest test suite
5. Verify CLI entry point
6. Check version consistency
7. Build and verify package

## Dependencies

### Runtime Dependencies (pyproject.toml)

- `mcp` - Model Context Protocol SDK
- `pydantic` - Data validation
- `starlette` - HTTP transport support
- `uvicorn` - ASGI server

### Development Dependencies

- `pytest` - Testing framework
- `twine` - Package verification

### Updating Dependencies

Dependabot automatically creates PRs for dependency updates weekly. Review and merge as appropriate.

## Troubleshooting

### CDB Not Found

The server looks for CDB in these locations:
- `C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe`
- `C:\Program Files\Debugging Tools for Windows\cdb.exe`
- Microsoft Store WinDbg location

Set `CDB_PATH` environment variable to use a custom path.

### Tests Skipped

If tests are skipped:
- Ensure CDB is installed and accessible
- Check that test dump files exist (run `git lfs pull`)

### Version Mismatch Errors

If CI fails on version consistency:
- Run `.\scripts\check-version-consistency.ps1` locally
- Ensure all three version locations are updated

