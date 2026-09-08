"""Corrupt arrangements must remain recoverable without touching live terminals."""

import copy
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces.application import launch
from tmux_workspaces.cli import parser
from tmux_workspaces.layout_validation import InvalidLayout, validate_state
from tmux_workspaces.model import Model, leaves
from tmux_workspaces.persistence import LibraryError, Store


def database(directory, value, table="terminal_layout"):
    path = directory / "layouts.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute(f"INSERT INTO {table} VALUES (1, ?)", (value,))
    return path


class LayoutRecoveryTests(unittest.TestCase):
    def malformed_states(self):
        model = Model.initial()
        model.split("right")
        original = model.state
        cases = [None, [], {}, {"version": 2, "workspaces": [{}]}]
        for field, value in (("version", 99), ("focus", "yes"), ("selected", "missing")):
            cases.append(original | {field: value})
        changes = [
            lambda s: s["workspaces"][0].pop("tabs"),
            lambda s: s["workspaces"][0].update(name=""),
            lambda s: s["workspaces"][0].update(id="invalid"),
            lambda s: s["workspaces"][0].update(selected="missing"),
            lambda s: s["workspaces"][0]["tabs"][0].update(focus="missing"),
            lambda s: s["workspaces"][0]["tabs"][0].update(tree=None),
            lambda s: s["workspaces"][0]["tabs"][0]["tree"].update(direction="diagonal"),
            lambda s: s["workspaces"][0]["tabs"][0]["tree"].update(ratio=True),
            lambda s: s["workspaces"][0]["tabs"][0]["tree"].update(ratio=None),
            lambda s: s["workspaces"][0]["tabs"][0]["tree"].update(ratio=float("nan")),
            lambda s: s["workspaces"][0]["tabs"][0]["tree"].update(ratio=10**1000),
            lambda s: s["workspaces"][0]["tabs"][0]["tree"].pop("first"),
            lambda s: leaves(s["workspaces"][0]["tabs"][0]["tree"])[0].pop("agent"),
            lambda s: leaves(s["workspaces"][0]["tabs"][0]["tree"])[0].update(agent="bad:target"),
            lambda s: leaves(s["workspaces"][0]["tabs"][0]["tree"])[0].update(cwd="relative"),
            lambda s: leaves(s["workspaces"][0]["tabs"][0]["tree"])[0].update(cwd="/bad\0path"),
            lambda s: leaves(s["workspaces"][0]["tabs"][0]["tree"])[0].update(
                source_socket="/socket"
            ),
        ]
        for change in changes:
            state = copy.deepcopy(original)
            change(state)
            cases.append(state)
        duplicate = copy.deepcopy(original)
        a, b = leaves(duplicate["workspaces"][0]["tabs"][0]["tree"])
        b["id"] = a["id"]
        cases.append(duplicate)
        return cases

    def test_malformed_records_leave_original_database_bytes_and_tables_unchanged(self):
        for index, state in enumerate(self.malformed_states()):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = database(root, json.dumps(state))
                before = path.read_bytes()
                with closing(Store(root)) as store, self.assertRaises(LibraryError) as caught:
                    store.load()
                self.assertEqual(path.read_bytes(), before)
                self.assertIn(str(path), str(caught.exception))
                self.assertIn("--data-dir", str(caught.exception))
                self.assertIn("back up", str(caught.exception))

    def test_invalid_json_and_excessive_nesting_are_actionable(self):
        values = [
            "{broken",
            "[" * 2000 + "0" + "]" * 2000,
            '{"version":99,"version":2,"workspaces":[]}',
            json.dumps(Model.initial().state | {"extension": float("nan")}),
        ]
        model = Model.initial()
        for _ in range(66):
            model.split("right")
        values.append(json.dumps(model.state))
        extra = Model.initial().state
        extra["extension"] = [[0]]
        for _ in range(150):
            extra["extension"] = [extra["extension"]]
        values.append(json.dumps(extra))
        for value in values:
            with self.subTest(value=value[:40]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = database(root, value)
                before = path.read_bytes()
                with closing(Store(root)) as store, self.assertRaises(LibraryError):
                    store.load()
                self.assertEqual(path.read_bytes(), before)

    def test_invalid_legacy_migration_creates_no_new_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = database(root, '{"version":1,"workspaces":[{}]}', "shared_layout")
            before = path.read_bytes()
            with closing(Store(root)) as store, self.assertRaises(LibraryError):
                store.load()
            self.assertEqual(path.read_bytes(), before)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                    [("shared_layout",)],
                )

    def test_valid_legacy_preserves_exact_record_and_attachment_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = Model.initial()
            model.attach("café session", "/tmp/original source.sock")
            model.state["version"] = 1
            model.pane.pop("cwd")
            value = json.dumps(model.state, indent=3, ensure_ascii=False)
            path = database(root, value, "layout")
            with closing(Store(root)) as store:
                restored = store.load()
                self.assertEqual(restored.state, model.state | {"version": 2})
                store.save(restored)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("SELECT value FROM layout WHERE id=1").fetchone()[0], value
                )

    def test_corrupt_current_row_blocks_refresh_and_save_without_overwriting_or_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with closing(Store(root)) as store:
                model = store.load()
                before_model, before_base = copy.deepcopy(model.state), copy.deepcopy(store.base)
                with closing(sqlite3.connect(store.path)) as db, db:
                    db.execute("UPDATE terminal_layout SET value='{broken' WHERE id=1")
                    db.execute("INSERT INTO layout VALUES (1, ?)", (json.dumps(before_model),))
                before = store.path.read_bytes()
                for action in (lambda: store.refresh(model), lambda: store.save(model)):
                    with self.assertRaises(LibraryError):
                        action()
                    self.assertEqual(model.state, before_model)
                    self.assertEqual(store.base, before_base)
                    self.assertEqual(store.path.read_bytes(), before)
                with closing(Store(root)) as reopened, self.assertRaises(LibraryError):
                    reopened.load()

    def test_missing_current_row_and_invalid_sqlite_are_not_initialized_over(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = database(root, json.dumps(Model.initial().state))
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DELETE FROM terminal_layout")
            before = path.read_bytes()
            with closing(Store(root)) as store, self.assertRaises(LibraryError):
                store.load()
            self.assertEqual(path.read_bytes(), before)
            path.write_bytes(b"not a sqlite database")
            before = path.read_bytes()
            with closing(Store(root)) as store, self.assertRaises(LibraryError):
                store.load()
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_library_preflight_starts_no_tmux_servers_even_in_demo_mode(self):
        for demo in (False, True):
            with self.subTest(demo=demo), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                library = root / "demo" if demo else root
                library.mkdir(exist_ok=True)
                path = database(library, "{broken")
                before = path.read_bytes()
                args = parser().parse_args(["--data-dir", str(root), *(["--demo"] if demo else [])])
                with (
                    patch("tmux_workspaces.preflight.check_terminal"),
                    patch("tmux_workspaces.preflight.shutil.which", return_value="tmux"),
                    patch(
                        "tmux_workspaces.application.Tmux",
                        side_effect=AssertionError("tmux started"),
                    ),
                    patch(
                        "tmux_workspaces.application.subprocess.run",
                        return_value=subprocess.CompletedProcess(["tmux", "-V"], 0, "tmux 3.3"),
                    ) as command,
                    self.assertRaises(LibraryError),
                ):
                    launch(args)
                command.assert_called_once_with(
                    ["tmux", "-V"], capture_output=True, text=True, timeout=5
                )
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((library / "windows").exists())

    def test_unknown_nested_cycles_are_rejected_before_recursive_operations(self):
        state = Model.initial().state
        state["extension"] = state
        with self.assertRaises(InvalidLayout):
            validate_state(state)

    def test_omitted_ratio_preserves_the_supported_half_split_default(self):
        for table in ("terminal_layout", "layout"):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                model = Model.initial()
                model.split("right")
                model.tab["tree"].pop("ratio")
                database(root, json.dumps(model.state), table)
                with closing(Store(root)) as store:
                    restored = store.load()
                    self.assertEqual(restored.state, model.state)
                    store.save(restored)
                    self.assertEqual(store.load().state, model.state)

    def test_write_size_limit_preserves_original_bytes_on_save_and_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with closing(Store(root)) as store:
                model = store.load()
                before = store.path.read_bytes()
                base = copy.deepcopy(store.base)
                limit = len(json.dumps(model.state).encode()) + 4
                model.state["extension"] = "x" * 100
                proposed = copy.deepcopy(model.state)
                with (
                    patch("tmux_workspaces.persistence.MAX_RECORD_BYTES", limit),
                    self.assertRaises(LibraryError),
                ):
                    store.save(model)
                self.assertEqual(store.path.read_bytes(), before)
                self.assertEqual(store.base, base)
                self.assertEqual(model.state, proposed)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = Model.initial().state | {"version": 1}
            compact = json.dumps(state, separators=(",", ":"))
            path = database(root, compact, "layout")
            before = path.read_bytes()
            # Reading fits, but normal serialization would add whitespace beyond
            # the same limit. Migration must fail before changing any table.
            with (
                patch("tmux_workspaces.persistence.MAX_RECORD_BYTES", len(compact.encode())),
                closing(Store(root)) as store,
                self.assertRaises(LibraryError),
            ):
                store.load()
            self.assertEqual(path.read_bytes(), before)
