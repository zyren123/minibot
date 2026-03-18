"""CLI entrypoint for `minibot-web`."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="minibot-web", description="Run the Minibot WebUI server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--workdir", default=None, help="Workspace directory (defaults to CWD).")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only).")
    args = parser.parse_args(argv)

    if args.workdir:
        os.chdir(str(Path(args.workdir).resolve()))

    import uvicorn

    uvicorn.run(
        "minibot.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
