#!/usr/bin/env python3
"""Convenience launcher for Ultimate LAB5 Flasher.

It creates a local virtual environment on first run, installs this project in
editable mode, and then forwards all arguments to ``python -m lab5_flasher``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
MARKER = VENV_DIR / ".ultimate-lab5-installed"


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix) or hasattr(sys, "real_prefix")


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv() -> Path:
    python = venv_python()
    if not python.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])

    pyproject = ROOT / "pyproject.toml"
    needs_install = not MARKER.exists()
    if MARKER.exists() and pyproject.exists():
        needs_install = MARKER.stat().st_mtime < pyproject.stat().st_mtime

    if needs_install:
        subprocess.check_call([str(python), "-m", "pip", "install", "--upgrade", "pip"])
        subprocess.check_call([str(python), "-m", "pip", "install", "-e", str(ROOT)])
        MARKER.write_text("installed\n", encoding="utf-8")

    return python


def main() -> None:
    if os.environ.get("ULTIMATE_FLASHER_NO_VENV") or in_virtualenv():
        from lab5_flasher.cli import main as cli_main

        raise SystemExit(cli_main())

    python = ensure_venv()
    os.execv(str(python), [str(python), "-m", "lab5_flasher", *sys.argv[1:]])


if __name__ == "__main__":
    main()
