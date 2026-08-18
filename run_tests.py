"""Run the project test suite using Python only."""
from __future__ import annotations

import sys

from runner_bootstrap import ensure_project_venv


if __name__ == "__main__":
    ensure_project_venv(__file__)
    import pytest

    print("Running tests with project .venv")
    user_args = [arg for arg in sys.argv[1:] if arg != "--cache-clear"]
    raise SystemExit(pytest.main(["-q", "-p", "no:cacheprovider", *user_args]))
