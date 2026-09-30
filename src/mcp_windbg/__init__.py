import os

from .server import serve, serve_http

INIT_COMMANDS_ENV = "MCP_WINDBG_INIT_COMMANDS"
KERNEL_INIT_COMMANDS_ENV = "MCP_WINDBG_KERNEL_INIT_COMMANDS"


def _resolve_init_commands(cli_commands, env_name=INIT_COMMANDS_ENV, environ=os.environ):
    """CLI flags win; otherwise the environment variable, one command per line."""
    if cli_commands:
        return cli_commands
    lines = environ.get(env_name, "").splitlines()
    return [line.strip() for line in lines if line.strip()]


def main():
    """MCP WinDbg Server - Windows crash dump analysis functionality for MCP"""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Give a model the ability to analyze Windows crash dumps with WinDbg/CDB"
    )
    parser.add_argument("--cdb-path", type=str, help="Custom path to cdb.exe")
    parser.add_argument("--kd-path", type=str, help="Custom path to kd.exe (kernel debugging)")
    parser.add_argument("--symbols-path", type=str, help="Custom symbols path")
    parser.add_argument("--filter-script", type=str, help="Path to a Python script with process_input/process_output tool text hooks")
    parser.add_argument("--timeout", type=int, default=60, help="Baseline command/connect timeout in seconds (floor for per-tool defaults)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--no-dump-dir-symbols", action="store_true",
                        help="Disable automatic inclusion of the dump file's directory in the symbol search path")
    parser.add_argument("--init-command", action="append", metavar="COMMAND",
                        help="Debugger command to run on every new session before triage, e.g. "
                             "'.load C:\\ext\\my.dll'. Repeatable. Defaults to the lines of "
                             f"{INIT_COMMANDS_ENV}")
    parser.add_argument("--kernel-init-command", action="append", metavar="COMMAND",
                        help="Like --init-command, but only for kernel targets (kd sessions and "
                             "kernel dumps). Repeatable. Defaults to the lines of "
                             f"{KERNEL_INIT_COMMANDS_ENV}")

    # Transport options
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport protocol to use (default: stdio)"
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind HTTP server to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind HTTP server to (default: 8000)")

    args = parser.parse_args()

    auto_dump_dir_symbols = not args.no_dump_dir_symbols
    init_commands = _resolve_init_commands(args.init_command)
    kernel_init_commands = _resolve_init_commands(args.kernel_init_command, KERNEL_INIT_COMMANDS_ENV)

    if args.transport == "stdio":
        asyncio.run(serve(
            cdb_path=args.cdb_path,
            kd_path=args.kd_path,
            symbols_path=args.symbols_path,
            filter_script=args.filter_script,
            timeout=args.timeout,
            verbose=args.verbose,
            auto_dump_dir_symbols=auto_dump_dir_symbols,
            init_commands=init_commands,
            kernel_init_commands=kernel_init_commands,
        ))
    else:  # pragma: no cover - HTTP transport is verified behaviorally (http_transport.yaml), not line-counted
        asyncio.run(serve_http(
            host=args.host,
            port=args.port,
            cdb_path=args.cdb_path,
            kd_path=args.kd_path,
            symbols_path=args.symbols_path,
            filter_script=args.filter_script,
            timeout=args.timeout,
            verbose=args.verbose,
            auto_dump_dir_symbols=auto_dump_dir_symbols,
            init_commands=init_commands,
            kernel_init_commands=kernel_init_commands,
        ))


if __name__ == "__main__":
    main()
