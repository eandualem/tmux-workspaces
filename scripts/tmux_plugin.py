#!/usr/bin/env python3
"""Compatibility command for pre-package launchers; keep while old viewers run."""

import sys
from pathlib import Path

# Direct file execution starts in this legacy directory, not the package root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tmux_workspaces.bootstrap import plugin_main as main

if __name__ == "__main__":
    raise SystemExit(main())
