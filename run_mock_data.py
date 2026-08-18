"""Convenient Python entry point for generating linked MongoDB mock data.

Usage:
    python run_mock_data.py
    python run_mock_data.py --rentals 45 --days 4
"""
from __future__ import annotations

import sys

from runner_bootstrap import ensure_project_venv


if __name__ == "__main__":
    ensure_project_venv(__file__)
    from scripts.seed_mock_data import main

    # Running this explicitly named entry point is the write confirmation.
    if "--confirm-seed" not in sys.argv:
        sys.argv.append("--confirm-seed")
    raise SystemExit(main())
