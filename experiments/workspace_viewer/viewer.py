#!/usr/bin/env python3
"""Compatibility command for pre-package launchers; keep while old viewers run."""

import sys
from pathlib import Path

# Direct file execution starts in this legacy directory, not the package root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tmux_workspaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
