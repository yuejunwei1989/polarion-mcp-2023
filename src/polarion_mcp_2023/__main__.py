"""CLI entry point."""

from __future__ import annotations

import argparse
import os
import sys

from .config import ConfigError
from .server import build_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="polarion-mcp-2023",
        description="MCP server for Siemens Polarion ALM, compatible with Polarion 2023 (2304/2310).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default=os.getenv("POLARION_MCP_TRANSPORT", "stdio"),
        help="Transport to serve (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("POLARION_MCP_HOST", "127.0.0.1"),
        help="Bind host for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("POLARION_MCP_PORT", "8000")),
        help="Bind port for HTTP transports (default: 8000).",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("POLARION_LOG_LEVEL", "INFO"),
        help="Log level: DEBUG, INFO, WARNING, ERROR (default: INFO).",
    )
    args = parser.parse_args(argv)

    try:
        mcp, runtime = build_server(host=args.host, port=args.port, log_level=args.log_level)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if runtime.startup_error is not None:
        print(
            f"Warning: startup probe failed: {runtime.startup_error}. "
            "The server is running; call polarion_server_info for details.",
            file=sys.stderr,
        )

    try:
        mcp.run(args.transport)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
