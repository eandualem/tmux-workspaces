"""SQLite library storage and concurrent edit reconciliation."""

import json
import sqlite3
from pathlib import Path

from .model import (
    LayoutConflict,
    Model,
    local_navigation,
    merge_layout,
    shared_layout,
    validate_library,
)


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "layouts.db"
        self.db = sqlite3.connect(self.path, timeout=5)
        # Older viewers know only agent tabs. Keep their writes out of this library.
        for table in ("layout", "shared_layout", "terminal_layout"):
            self.db.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, value TEXT)"
            )
        self.base: dict | None = None

    def load(self) -> Model:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT value FROM terminal_layout WHERE id = 1").fetchone()
            if not row:
                old = self.db.execute("SELECT value FROM shared_layout WHERE id = 1").fetchone()
                old = old or self.db.execute("SELECT value FROM layout WHERE id = 1").fetchone()
                state = json.loads(old[0]) if old else Model.initial().state
                if state.get("version") not in (1, 2) or not state.get("workspaces"):
                    raise ValueError("Unsupported workspace layout. Keep layouts.db for recovery.")
                # Preserve every existing name, attachment and split. No roster seeding.
                state["version"] = 2
                value = json.dumps(state)
                self.db.execute("INSERT INTO terminal_layout VALUES (1, ?)", (value,))
                row = (value,)
        state = json.loads(row[0])
        if state.get("version") != 2 or not state.get("workspaces"):
            raise ValueError("Unsupported workspace layout. Keep layouts.db for recovery.")
        self.base = shared_layout(state)
        return Model(state)

    def save(self, model: Model) -> None:
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute("SELECT value FROM terminal_layout WHERE id = 1").fetchone()
                latest = shared_layout(json.loads(row[0])) if row else shared_layout(model.state)
                merged = merge_layout(
                    self.base if self.base is not None else latest,
                    shared_layout(model.state),
                    latest,
                )
                validate_library(merged)
                state = local_navigation(merged, model.state)
                self.db.execute(
                    "INSERT OR REPLACE INTO terminal_layout VALUES (1, ?)", (json.dumps(state),)
                )
            self.base, model.state = shared_layout(state), state
        except LayoutConflict:
            self.base = latest
            model.state = local_navigation(latest, model.state)
            raise

    def refresh(self, model: Model) -> bool:
        row = self.db.execute("SELECT value FROM terminal_layout WHERE id = 1").fetchone()
        latest = shared_layout(json.loads(row[0]))
        if latest == self.base:
            return False
        try:
            merged = merge_layout(self.base, shared_layout(model.state), latest)
            validate_library(merged)
        except LayoutConflict:
            self.base = latest
            model.state = local_navigation(latest, model.state)
            raise
        self.base = latest
        model.state = local_navigation(merged, model.state)
        return True

    def close(self) -> None:
        self.db.close()
