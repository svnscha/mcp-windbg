# Changelog

All notable changes to the MCP Server for WinDBG Crash Analysis project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2025-10-10

### Added

#### CI/CD Integration

- **GitHub Actions Workflow**: Automated CI pipeline for pull requests and pushes
  - Multi-Python version testing (3.10, 3.11, 3.12)
  - Windows runner with automatic Windows Debugging Tools detection
  - Syntax checking, testing, and package building
  - Manual workflow dispatch support

- **Dependabot Configuration**: Automated dependency updates
  - Weekly Python package updates with security patches
  - GitHub Actions version updates
  - Automatic pull request creation with CI validation

#### Python Integration

- **Modern Dependency Management**: Migrated from pip to uv
  - Fast dependency resolution and installation (10-100x speedup)
  - Reproducible builds with uv.lock file
  - Built-in virtual environment management
  - Modern dependency-groups configuration

- **Enhanced Development Workflow**: Streamlined development commands
  - `uv sync --dev` for dependency installation
  - `uv run` for managed environment execution
  - `uv build` for package building

#### Developer Experience
- Comprehensive Windows debugging agent instructions in `AGENTS.md`
- Structured dump triage prompt for systematic crash analysis
- Advanced prompt templates for guided debugging workflows

### Improved
- Enhanced documentation with better integration examples
- Expanded troubleshooting section with common symbol path configurations
- **CI Performance**: Significantly faster build times with uv integration
- **Dependency Security**: Automated security updates via Dependabot

## [0.1.0] - 2025-05-03

### Added
- **Core MCP Server Implementation**
  - Model Context Protocol server for Windows debugging tools
  - Integration with WinDBG/CDB for crash dump analysis and remote debugging
  - Python 3.10+ support with modern async architecture

- **Crash Dump Analysis Tools**
  - `open_windbg_dump`: Comprehensive crash dump analysis with `!analyze -v` output
  - `run_windbg_cmd`: Execute any WinDBG command on loaded dump files
  - `close_windbg_dump`: Proper resource cleanup for dump sessions
  - Support for stack traces, module information, and thread analysis

- **Remote Debugging Capabilities**
  - `open_windbg_remote`: Connect to live debugging sessions via TCP
  - `close_windbg_remote`: Clean disconnect from remote debugging targets
  - Real-time command execution on live processes

- **File Discovery and Management**
  - `list_windbg_dumps`: Discover crash dumps in specified directories
  - **Automatic Local Dump Detection**: Find dumps in Windows registry-configured paths (contributed by @jeff)
  - Support for common dump file extensions (.dmp)
  - Recursive directory scanning capabilities

- **VS Code Integration**
  - Complete MCP configuration for VS Code Copilot integration
  - Agent mode support with natural language debugging queries
  - Seamless integration with GitHub Copilot's Model Context Protocol features

- **Advanced Configuration**
  - Customizable CDB executable path
  - Configurable symbol paths with Microsoft symbol server defaults
  - Timeout controls for long-running analysis operations
  - Verbose logging for debugging server issues

- **Documentation and Examples**
  - Comprehensive README with setup instructions
  - VS Code integration guide with sample configurations
  - Symbol server configuration examples
  - Natural language query examples for both dump analysis and remote debugging

### Technical Features
- **Robust Error Handling**: Comprehensive error management for CDB interactions
- **Resource Management**: Proper session lifecycle management with cleanup
- **Cross-Platform Foundation**: Windows-focused with extensible architecture
- **Testing Framework**: pytest-based test suite for reliability

### Development Tools
- **Modern Python Packaging**: pyproject.toml configuration with setuptools
- **Development Dependencies**: Isolated test dependencies for clean development
- **MIT License**: Open source license for community contributions

---

## Contributors

- **@svnscha** - Initial implementation and core architecture
- **@jeff** - Improvement: Find local dumps with other common extensions (#6)