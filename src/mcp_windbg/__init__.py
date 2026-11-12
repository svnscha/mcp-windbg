import os

from .http_security import AUTH_TOKEN_ENV, check_http_exposure
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
    parser.add_argument("--verbose", action="store_true",
                        help="Log debugger traffic and server diagnostics to stderr")
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
    parser.add_argument("--log-dir", type=str, metavar="DIR",
                        help="Write every line each debugger session prints to DIR/<session_id>.log, "
                             "unfiltered, for read_debugger_log. Off by default")

    parser.add_argument("--restrict-commands", action="store_true",
                        help="Refuse debugger commands that reach past the target - .shell, .load, "
                             ".writemem, .server, script files, aliases and similar - and network "
                             "paths in tool arguments. Guards against instructions planted in a "
                             "dump. --init-command is not restricted")

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
    parser.add_argument("--allowed-host", action="append", metavar="HOST",
                        help="Extra Host header value the HTTP server accepts, e.g. 'debugbox' or "
                             "'debugbox:8000' (no port means any port). Repeatable. Loopback names "
                             "and a specific --host are always accepted")
    parser.add_argument("--auth-token", type=str,
                        help="Require 'Authorization: Bearer <token>' on HTTP requests. Prefer the "
                             f"{AUTH_TOKEN_ENV} environment variable, which other users cannot see "
                             "in the process list")
    parser.add_argument("--allow-unauthenticated", action="store_true",
                        help="Allow binding a non-loopback --host without an auth token")

    args = parser.parse_args()

    if args.verbose:
        # Without this there is no handler at all, so every logger.info in the
        # server and in a --filter-script was discarded and --verbose only ever
        # reached the debugger traffic. stderr, because on the stdio transport
        # stdout carries the JSON-RPC stream and nothing else may go there.
        import logging
        import sys

        logging.basicConfig(
            stream=sys.stderr,
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
        )

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
            log_dir=args.log_dir,
            restrict_commands=args.restrict_commands,
        ))
    else:  # pragma: no cover - HTTP transport is verified behaviorally (http_transport.yaml), not line-counted
        auth_token = args.auth_token or os.environ.get(AUTH_TOKEN_ENV) or None
        check_http_exposure(args.host, auth_token, args.allow_unauthenticated)
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
            log_dir=args.log_dir,
            restrict_commands=args.restrict_commands,
            allowed_hosts=args.allowed_host,
            auth_token=auth_token,
        ))


if __name__ == "__main__":
    main()
