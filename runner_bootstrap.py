"""Make root runner scripts consistently use this project's virtual environment."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def ensure_project_venv(script_path: str) -> None:
    """Re-run a root entry script with `.venv` Python when necessary."""
    if os.name == "nt":
        venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"

    if not venv_python.exists():
        raise SystemExit(
            "Project .venv was not found. Run: python -m venv .venv\n"
            "Then install: .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        )

    try:
        current_python = Path(sys.executable).resolve()
        expected_python = venv_python.resolve()
    except OSError:
        return
    if current_python != expected_python:
        absolute_script = str(Path(script_path).resolve())
        completed = subprocess.run(
            [str(expected_python), absolute_script, *sys.argv[1:]],
            cwd=PROJECT_ROOT,
            check=False,
        )
        raise SystemExit(completed.returncode)
