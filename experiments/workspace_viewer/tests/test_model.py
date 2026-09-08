import copy
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import LayoutConflict, Model, Store, leaves


def with_agents(names):
    """Explicit user-created fixtures; production never seeds the agent roster."""
    model = Model.initial()
    model.close_tab()
    for name in names:
        model.add_tab(name)
        model.attach(name)
    if names:
        model.space["selected"] = model.space["tabs"][0]["id"]
    return model


def seeded(store, names):
    model = store.load()
    model.state = with_agents(names).state
    store.save(model)
    return model


def split_agent(model, name, direction):
    model.split(direction)
    model.attach(name)


class LayoutTests(unittest.TestCase):
    def test_new_library_is_one_neutrally_named_normal_terminal(self):
        model = Model.initial()
        self.assertEqual(len(model.space["tabs"]), 1)
        self.assertTrue(model.tab["name"].startswith("Tab "))
        self.assertIsNone(model.pane["agent"])
        self.assertEqual(model.pane["cwd"], str(Path.cwd()))
        original = model.tab["name"]
        model.add_tab()
        self.assertNotEqual(model.tab["name"], original)

    def test_attachment_preserves_name_terminal_identity_and_other_panes(self):
        model = Model.initial()
        model.tab["name"] = "My organizers"
        first = copy.deepcopy(model.pane)
        model.attach("manager", "/tmp/source.sock")
        self.assertEqual(model.tab["name"], "My organizers")
        self.assertEqual(model.pane["id"], first["id"])
        self.assertEqual(model.pane["source_socket"], "/tmp/source.sock")
        model.split("right", "/tmp")
        self.assertIsNone(model.pane["agent"])
        self.assertEqual(model.pane["cwd"], "/tmp")
        model.split("below")
        model.split("right")
        self.assertEqual(len(model.space["tabs"]), 1)
        self.assertEqual(
            [p["agent"] for p in leaves(model.tab["tree"])], ["manager", None, None, None]
        )
        model.tab["focus"] = first["id"]
        model.attach(None)
        self.assertEqual(model.pane, first)
        self.assertEqual(model.tab["name"], "My organizers")

    def test_split_belongs_to_tab_and_workspace_remembers_selection(self):
        model = with_agents(["manager", "research"])
        first = model.space
        split_agent(model, "builder", "right")
        split_agent(model, "reviewer", "below")
        selected = first["selected"]
        self.assertEqual(len(first["tabs"]), 2)
        self.assertEqual(
            [item["agent"] for item in leaves(model.tab["tree"])],
            ["manager", "builder", "reviewer"],
        )
        model.add_workspace("Release")
        model.add_tab("manager")
        model.state["selected"] = first["id"]
        self.assertEqual(model.tab["id"], selected)
        self.assertEqual(len(leaves(model.tab["tree"])), 3)

    def test_focus_roundtrip_and_offline_references_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            model = seeded(store, ["agent-that-may-go-offline"])
            split_agent(model, "worker", "right")
            model.attach("Ops [α] 'x'", "/tmp/original-source.sock")
            tree = copy.deepcopy(model.tab["tree"])
            model.state["focus"] = True
            model.next_pane()
            store.save(model)
            store.close()
            store = Store(Path(directory))
            try:
                reopened = store.load()
                self.assertEqual(reopened.tab["tree"], tree)
                self.assertTrue(reopened.state["focus"])
                self.assertEqual(reopened.tab["focus"], model.tab["focus"])
            finally:
                store.close()

    def test_close_pane_collapses_tree_without_duplicate_tabs(self):
        model = with_agents(["one"])
        split_agent(model, "two", "right")
        split_agent(model, "three", "below")
        model.close_pane()
        self.assertEqual([x["agent"] for x in leaves(model.tab["tree"])], ["one", "two"])
        model.close_pane()
        self.assertEqual([x["agent"] for x in leaves(model.tab["tree"])], ["two"])
        model.close_pane()
        self.assertIsNone(model.tab)
        self.assertEqual(len(model.state["workspaces"]), 1)

    def test_two_windows_share_edits_but_keep_independent_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Store(Path(directory)), Store(Path(directory))
            self.addCleanup(one.close)
            self.addCleanup(two.close)
            a, b = seeded(one, ["manager", "researcher"]), two.load()
            original = a.tab["id"]
            b.space["selected"] = b.space["tabs"][1]["id"]
            b.state["focus"] = True
            two.save(b)
            self.assertFalse(one.refresh(a))
            self.assertEqual(a.tab["id"], original)
            self.assertFalse(a.state["focus"])
            a.add_tab("builder")
            b.add_tab("reviewer")
            one.save(a)
            two.save(b)
            one.refresh(a)
            self.assertEqual(
                [t["name"] for t in a.space["tabs"]],
                ["manager", "researcher", "builder", "reviewer"],
            )
            self.assertEqual(a.tab["name"], "builder")
            self.assertEqual(b.tab["name"], "reviewer")
            self.assertTrue(b.state["focus"])

    def test_stale_window_cannot_resurrect_deleted_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Store(Path(directory)), Store(Path(directory))
            self.addCleanup(one.close)
            self.addCleanup(two.close)
            a, b = seeded(one, ["manager", "researcher"]), two.load()
            one.save(a)
            a.close_tab()
            one.save(a)
            b.add_workspace("Release")
            two.save(b)
            one.refresh(a)
            self.assertEqual([t["name"] for t in a.space["tabs"]], ["researcher"])
            self.assertEqual(len(a.state["workspaces"]), 2)

    def test_conflicting_split_edits_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Store(Path(directory)), Store(Path(directory))
            self.addCleanup(one.close)
            self.addCleanup(two.close)
            a, b = seeded(one, ["manager"]), two.load()
            split_agent(a, "builder", "right")
            split_agent(b, "reviewer", "below")
            one.save(a)
            with self.assertRaises(LayoutConflict):
                two.save(b)
            self.assertEqual([x["agent"] for x in leaves(b.tab["tree"])], ["manager", "builder"])
            split_agent(b, "reviewer", "below")
            two.save(b)
            one.refresh(a)
            self.assertEqual(len(leaves(a.tab["tree"])), 3)

    def test_old_window_cannot_overwrite_migrated_layouts(self):
        import json
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            original = with_agents(["manager"])
            with closing(sqlite3.connect(path / "layouts.db")) as db, db:
                db.execute("CREATE TABLE layout (id INTEGER PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO layout VALUES (1, ?)", (json.dumps(original.state),))
            store = Store(path)
            self.addCleanup(store.close)
            model = store.load()
            self.assertEqual(model.tab["id"], original.tab["id"])
            model.add_tab("builder")
            store.save(model)
            with closing(sqlite3.connect(path / "layouts.db")) as db, db:
                db.execute("UPDATE layout SET value = ?", (json.dumps(original.state),))
            self.assertFalse(store.refresh(model))
            self.assertEqual(len(model.space["tabs"]), 2)

    def test_agent_only_multiwindow_library_preserves_custom_layouts_on_upgrade(self):
        import json
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            original = with_agents(["manager"])
            original.space["name"] = "My home setup"
            original.tab["name"] = "Organizers"
            split_agent(original, "offline-worker", "below")
            for pane in leaves(original.tab["tree"]):
                pane.pop("cwd")  # The actual older representation had no ordinary shells.
            original.state["version"] = 1
            with closing(sqlite3.connect(path / "layouts.db")) as db, db:
                db.execute("CREATE TABLE shared_layout (id INTEGER PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO shared_layout VALUES (1, ?)", (json.dumps(original.state),))
            store = Store(path)
            self.addCleanup(store.close)
            model = store.load()
            self.assertEqual(model.state, original.state | {"version": 2})
            model.add_tab()
            store.save(model)
            with closing(sqlite3.connect(path / "layouts.db")) as db, db:
                db.execute("UPDATE shared_layout SET value = ?", (json.dumps(original.state),))
            self.assertFalse(store.refresh(model))
            self.assertEqual(len(model.space["tabs"]), 2)
            self.assertIsNone(model.pane["agent"])

    def test_same_tab_cannot_be_moved_to_two_destinations_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Store(Path(directory)), Store(Path(directory))
            self.addCleanup(one.close)
            self.addCleanup(two.close)
            a = seeded(one, ["manager"])
            original = a.space["id"]
            a.add_workspace("Build")
            a.add_workspace("Research")
            a.state["selected"] = original
            one.save(a)
            b = two.load()
            a.state["workspaces"][1]["tabs"].append(a.space["tabs"].pop())
            b.state["workspaces"][2]["tabs"].append(b.space["tabs"].pop())
            one.save(a)
            with self.assertRaises(LayoutConflict):
                two.save(b)
            self.assertEqual(sum(len(s["tabs"]) for s in b.state["workspaces"]), 1)

    def test_concurrent_reordering_requires_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Store(Path(directory)), Store(Path(directory))
            self.addCleanup(one.close)
            self.addCleanup(two.close)
            a, b = seeded(one, ["one", "two", "three"]), two.load()
            a.space["tabs"].reverse()
            b.space["tabs"].append(b.space["tabs"].pop(0))
            one.save(a)
            with self.assertRaises(LayoutConflict):
                two.save(b)


if __name__ == "__main__":
    unittest.main()
