"""SQLite library storage and concurrent edit reconciliation."""

import json
import sqlite3
from pathlib import Path

from .layout_validation import InvalidLayout, validate_state
from .model import (
    LayoutConflict,
    Model,
    local_navigation,
    merge_layout,
    shared_layout,
    validate_library,
)

TABLES = ("terminal_layout", "shared_layout", "layout")
MAX_RECORD_BYTES = 16 * 1024 * 1024


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidLayout("JSON object contains a duplicate field")
        result[key] = value
    return result


class LibraryError(ValueError):
    """Actionable failure that never asks the caller to discard its arrangement."""


class Store:
    def __init__(self, directory: Path):
        self.path = directory / "layouts.db"
        self.base: dict | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._existing = self.path.exists() and self.path.stat().st_size > 0
            self.db = sqlite3.connect(self.path, timeout=5)
        except (OSError, sqlite3.Error) as error:
            raise self.failure("cannot open the database") from error

    def failure(self, detail: str) -> LibraryError:
        return LibraryError(
            f"Workspace library {self.path}: {detail}. No automatic repair was attempted. "
            "Keep this database and its -wal/-shm files; back up the library before repair. "
            "Use --data-dir with a new directory to work separately. See docs/RECOVERY.md."
        )

    def _decode(self, value, *, legacy=False) -> dict:
        try:
            if not isinstance(value, str) or len(value.encode()) > MAX_RECORD_BYTES:
                raise InvalidLayout("layout record must be text no larger than 16 MiB")
            state = json.loads(value, object_pairs_hook=_json_object)
            validate_state(state, legacy=legacy)
            return state
        except (ValueError, TypeError, RecursionError) as error:
            detail = (
                str(error) if isinstance(error, InvalidLayout) else "invalid JSON layout record"
            )
            raise self.failure(detail) from error

    def _current(self) -> dict:
        row = self.db.execute("SELECT value FROM terminal_layout WHERE id = 1").fetchone()
        if row is None:
            raise self.failure("current layout row is missing")
        return self._decode(row[0])

    def load(self) -> Model:
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                tables = {
                    row[0]
                    for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                selected = next((table for table in TABLES if table in tables), None)
                if selected:
                    row = self.db.execute(f"SELECT value FROM {selected} WHERE id = 1").fetchone()
                    if row is None:
                        raise self.failure(f"{selected} layout row is missing")
                    state = self._decode(row[0], legacy=selected != "terminal_layout")
                elif self._existing or tables:
                    raise self.failure("no recognized workspace layout table")
                else:
                    state = Model.initial().state
                if selected != "terminal_layout":
                    # Only validated legacy/new data reaches schema creation. Keep
                    # every earlier table and its exact stored record unchanged.
                    state["version"] = 2
                    for table in TABLES:
                        self.db.execute(
                            f"CREATE TABLE IF NOT EXISTS {table} "
                            "(id INTEGER PRIMARY KEY, value TEXT)"
                        )
                    self.db.execute(
                        "INSERT INTO terminal_layout VALUES (1, ?)", (json.dumps(state),)
                    )
            self.base = shared_layout(state)
            return Model(state)
        except sqlite3.Error as error:
            raise self.failure(
                "database is unreadable, locked, or has an incompatible schema"
            ) from error

    def save(self, model: Model) -> None:
        try:
            validate_state(model.state, navigation=False)
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                latest = shared_layout(self._current())
                merged = merge_layout(
                    self.base if self.base is not None else latest,
                    shared_layout(model.state),
                    latest,
                )
                validate_library(merged)
                state = local_navigation(merged, model.state)
                validate_state(state)
                value = json.dumps(state, allow_nan=False)
                self.db.execute("INSERT OR REPLACE INTO terminal_layout VALUES (1, ?)", (value,))
            self.base, model.state = shared_layout(state), state
        except LayoutConflict:
            self.base = latest
            model.state = local_navigation(latest, model.state)
            raise
        except InvalidLayout as error:
            raise self.failure(str(error)) from error
        except LibraryError:
            raise
        except (sqlite3.Error, ValueError, TypeError, RecursionError) as error:
            raise self.failure(
                "cannot safely save the layout; database or record is invalid"
            ) from error

    def refresh(self, model: Model) -> bool:
        try:
            latest = shared_layout(self._current())
            if latest == self.base:
                return False
            try:
                merged = merge_layout(self.base, shared_layout(model.state), latest)
                validate_library(merged)
            except LayoutConflict:
                self.base = latest
                model.state = local_navigation(latest, model.state)
                raise
            state = local_navigation(merged, model.state)
            validate_state(state)
            self.base, model.state = latest, state
            return True
        except InvalidLayout as error:
            raise self.failure(str(error)) from error
        except sqlite3.Error as error:
            raise self.failure(
                "cannot refresh the current layout; database or schema is invalid"
            ) from error

    def close(self) -> None:
        self.db.close()
