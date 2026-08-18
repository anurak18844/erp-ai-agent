"""Start the FastAPI development server using Python only."""
from __future__ import annotations

import argparse
import sys

from runner_bootstrap import ensure_project_venv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ERP AI Agent FastAPI server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true", help="Disable development auto-reload")
    return parser.parse_args()


def main() -> None:
    ensure_project_venv(__file__)
    import uvicorn

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )


if __name__ == "__main__":
    main()
