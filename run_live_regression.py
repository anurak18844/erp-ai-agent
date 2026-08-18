"""Run live /chat regression through the project virtualenv."""
from __future__ import annotations

from runner_bootstrap import ensure_project_venv


if __name__ == "__main__":
    ensure_project_venv(__file__)
    from scripts.live_regression import main

    raise SystemExit(main())
