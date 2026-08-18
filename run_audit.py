"""Run the read-only linked mock-data audit using the project virtualenv."""
from __future__ import annotations

from runner_bootstrap import ensure_project_venv


if __name__ == "__main__":
    ensure_project_venv(__file__)
    from scripts.audit_mock_data import main

    raise SystemExit(main())
