"""Seed a separate example library for reviewing the compact UI branch."""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def seed(directory: Path) -> None:
    from .model import leaves
    from .persistence import Store

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "preview.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (directory / "demo/layouts.db").exists():
            return
        store = Store(directory / "demo")
        try:
            model = store.load()
            model.space["name"] = "Personal"
            model.tab["name"] = "shell"
            model.add_workspace("Development")
            development = model.space
            for name in ("shell", "api", "agents", "tests", "docs", "scratch"):
                model.add_tab(name)
                if name == "agents":
                    agents = model.tab
                    model.attach("manager")
                    model.split("right")
                    model.attach("builder")
                    model.split("below")
                    model.split("right")
                    agents["focus"] = leaves(agents["tree"])[0]["id"]
                elif name in {"api", "tests"}:
                    model.split("right")
            development["selected"] = agents["id"]
            model.add_workspace("Notes")
            model.add_tab("notes")
            model.state["selected"] = development["id"]
            store.save(model)
        finally:
            store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", action="store_true", help="run in the current terminal")
    options = parser.parse_args()
    directory = ROOT / ".backbone/ui-preview"
    seed(directory)
    launcher = ROOT / ("run" if options.terminal else "ghostty")
    os.execv(
        sys.executable, [sys.executable, str(launcher), "--data-dir", str(directory), "--demo"]
    )


if __name__ == "__main__":
    main()
