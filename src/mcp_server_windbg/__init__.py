from .server import serve

def main():
    """MCP WinDBG Server - Windows crash dump analysis functionality for MCP"""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Give a model the ability to analyze Windows crash dumps with WinDBG/CDB"
    )
    parser.add_argument("--cdb-path", type=str, help="Custom path to cdb.exe")
    parser.add_argument("--symbols-path", type=str, help="Custom symbols path")
    parser.add_argument("--timeout", type=int, default=30, help="Command timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()
    asyncio.run(serve(
        cdb_path=args.cdb_path,
        symbols_path=args.symbols_path,
        timeout=args.timeout,
        verbose=args.verbose
    ))


if __name__ == "__main__":
    main()
