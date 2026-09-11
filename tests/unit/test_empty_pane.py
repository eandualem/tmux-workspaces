"""A new tab is an empty pane that chooses a shell or a session in place."""

import contextlib
import shlex
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tmux_workspaces.application import roster_args
from tmux_workspaces.cli import parser
from tmux_workspaces.controls import pane_choice, valid_action
from tmux_workspaces.display import Display
from tmux_workspaces.keymap import DEFAULT_KEYMAP, Keymap
from tmux_workspaces.layout_validation import InvalidLayout, validate_state
from tmux_workspaces.model import LayoutConflict, Model, is_empty, leaves, new_tab
from tmux_workspaces.sidebar import Sidebar


class ModelTests(unittest.TestCase):
    def test_a_new_tab_can_open_empty_while_the_first_library_tab_is_a_shell(self):
        self.assertFalse(is_empty(Model.initial().pane))
        self.assertFalse(is_empty(new_tab()["tree"]))
        model = Model.initial()
        model.add_tab(empty=True)
        self.assertTrue(is_empty(model.pane))
        self.assertIsNone(model.pane["agent"])
        validate_state(model.state)

    def test_every_choice_ends_the_empty_state(self):
        for choice in ("terminal", "session", "return"):
            with self.subTest(choice=choice):
                model = Model.initial()
                model.add_tab(empty=True)
                if choice == "terminal":
                    model.open_terminal()
                elif choice == "session":
                    model.attach("work", "/tmp/source.sock")
                else:
                    model.attach(None)
                self.assertNotIn("empty", model.pane)
                self.assertFalse(is_empty(model.pane))
                self.assertEqual(model.pane["agent"], "work" if choice == "session" else None)

    def test_a_split_can_open_empty_and_keeps_the_directory_for_its_terminal(self):
        model = Model.initial()
        model.split("right", "/tmp", empty=True)
        self.assertTrue(is_empty(model.pane))
        self.assertEqual(model.pane["cwd"], "/tmp")
        # Fixtures that build libraries directly still get shells by default.
        model.split("below", "/var")
        self.assertFalse(is_empty(model.pane))

    def test_saved_layouts_accept_only_a_true_empty_flag_without_an_attachment(self):
        model = Model.initial()
        model.add_tab(empty=True)
        validate_state(model.state)
        for corruption in ({"empty": False}, {"empty": "yes"}, {"empty": True, "agent": "work"}):
            with self.subTest(corruption=corruption):
                model.pane.update(corruption)
                with self.assertRaises(InvalidLayout):
                    validate_state(model.state)
                model.pane.update({"empty": True, "agent": None})


class ControlTests(unittest.TestCase):
    tab, leaf = "0123456789ab", "ba9876543210"

    def test_pane_choices_are_decoded_and_bounded(self):
        terminal = f"choose-terminal:{self.tab}:{self.leaf}"
        self.assertEqual(pane_choice(terminal), ("choose-terminal", self.tab, self.leaf, None))
        session = f"choose-session:{self.tab}:{self.leaf}:-Ops [α] 'x'; $value"
        self.assertEqual(pane_choice(session)[3], "-Ops [α] 'x'; $value")
        self.assertTrue(valid_action(terminal) and valid_action(session))
        for bad in (
            f"choose-terminal:{self.tab}:{self.leaf}:extra",
            f"choose-session:{self.tab}:{self.leaf}",
            f"choose-session:{self.tab}:{self.leaf}:",
            f"choose-session:{self.tab}:{self.leaf}:has\ttab",
            f"choose-session:{self.tab[:-1]}:{self.leaf}:work",
            f"choose-session:{self.tab}:{self.leaf}:" + "n" * 5000,
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(pane_choice(bad))
                self.assertFalse(valid_action(bad))

    def test_a_pane_choice_cannot_be_bound_to_a_key(self):
        with self.assertRaisesRegex(ValueError, "unknown action"):
            Keymap.from_dict({"bindings": {f"choose-terminal:{self.tab}:{self.leaf}": ["t"]}})


class DisplayTests(unittest.TestCase):
    def display(self, *roster):
        return Display(
            "/tmp/view.sock",
            "/tmp/source.sock",
            "%0",
            "/tmp/shells.sock",
            "/tmp/action.sock",
            roster_args=roster,
        )

    def test_an_empty_pane_runs_a_chooser_that_names_its_tab_and_leaf(self):
        display = self.display("--demo", "--instance-dir", "/tmp/instance")
        model = Model.initial()
        model.add_tab(empty=True)
        display._tab_id = model.tab["id"]
        args = parser().parse_args(shlex.split(display._leaf_command(model.pane))[2:])
        self.assertTrue(args.chooser)
        self.assertEqual((args.tab, args.leaf), (model.tab["id"], model.pane["id"]))
        self.assertEqual(args.action_socket, "/tmp/action.sock")
        self.assertEqual(args.source_socket, "/tmp/source.sock")
        self.assertTrue(args.demo)
        self.assertEqual(str(args.instance_dir), "/tmp/instance")

    def test_filling_a_pane_changes_its_layout_key_so_the_client_is_replaced(self):
        model = Model.initial()
        model.add_tab(empty=True)
        before = Display._layout_key(model.tab["tree"])
        model.open_terminal()
        self.assertNotEqual(before, Display._layout_key(model.tab["tree"]))

    def test_roster_options_follow_the_sidebar_selection(self):
        base = {
            "demo": False,
            "backbone": False,
            "instance_dir": "/i",
            "backbone_data_dir": "/b",
            "url": None,
        }
        self.assertEqual(roster_args(SimpleNamespace(**base)), ())
        self.assertEqual(
            roster_args(SimpleNamespace(**{**base, "demo": True})),
            ("--demo", "--instance-dir", "/i"),
        )
        self.assertEqual(
            roster_args(SimpleNamespace(**{**base, "backbone": True, "url": "http://x"})),
            ("--backbone", "--backbone-data-dir", "/b", "--url", "http://x"),
        )


class SidebarTests(unittest.TestCase):
    def setUp(self):
        self.model = Model.initial()
        self.model.add_tab(empty=True)
        self.tab_id, self.leaf_id = self.model.tab["id"], self.model.pane["id"]
        screen = Mock()
        screen.getmaxyx.return_value = (38, 28)
        self.source = Mock(socket="/tmp/source.sock", persistent_socket=True)
        self.source.snapshot.return_value = ({}, "")
        self.display = Mock(
            sidebar="%0", small=False, keymap=DEFAULT_KEYMAP, panes={self.leaf_id: "%1"}
        )
        self.display.snapshot_scope.return_value = contextlib.nullcontext()
        self.display.focused_leaf.return_value = self.leaf_id
        self.store = Mock()
        self.sidebar = Sidebar(screen, self.model, self.store, self.source, self.display, Mock())

    def test_the_new_tab_button_opens_an_empty_pane(self):
        self.sidebar.new_tab()
        self.assertTrue(is_empty(self.model.pane))
        self.assertEqual(len(self.model.space["tabs"]), 3)

    def test_a_split_opens_an_empty_pane_beside_the_original_directory(self):
        self.model.open_terminal()
        self.model.pane["cwd"] = "/tmp/work"
        self.sidebar.split("right")
        self.assertTrue(is_empty(self.model.pane))
        self.assertEqual(self.model.pane["cwd"], "/tmp/work")
        self.assertEqual(len(leaves(self.model.tab["tree"])), 2)

    def test_choosing_a_terminal_fills_the_pane_and_redraws(self):
        self.sidebar.action(f"choose-terminal:{self.tab_id}:{self.leaf_id}")
        self.assertFalse(is_empty(self.model.pane))
        self.assertIsNone(self.model.pane["agent"])
        self.display.render.assert_called()
        self.store.save.assert_called()

    def test_choosing_a_session_attaches_it_with_the_roster_socket(self):
        self.sidebar.action(f"choose-session:{self.tab_id}:{self.leaf_id}:work")
        self.assertEqual(self.model.pane["agent"], "work")
        self.assertEqual(self.model.pane["source_socket"], "/tmp/source.sock")
        self.assertNotIn("empty", self.model.pane)

    def test_a_choice_for_a_pane_that_moved_is_refused_and_the_pane_redrawn(self):
        cases = {
            "other tab": lambda: self.sidebar.action(f"choose-terminal:{'0' * 12}:{self.leaf_id}"),
            "other leaf": lambda: self.sidebar.action(f"choose-terminal:{self.tab_id}:{'0' * 12}"),
            "not displayed": lambda: (
                self.display.panes.clear(),
                self.sidebar.action(f"choose-terminal:{self.tab_id}:{self.leaf_id}"),
            ),
            "peer conflict": lambda: (
                setattr(self.store.refresh, "side_effect", LayoutConflict("changed")),
                self.sidebar.action(f"choose-session:{self.tab_id}:{self.leaf_id}:work"),
            ),
        }
        for name, attempt in cases.items():
            with self.subTest(case=name):
                self.setUp()
                attempt()
                self.assertTrue(is_empty(self.model.pane), "the pane was filled anyway")
                self.assertEqual(self.sidebar.message, "Pane changed; choose again")
                self.display.render.assert_called()

    def test_a_filled_pane_refuses_a_late_choice(self):
        self.model.open_terminal()
        self.sidebar.action(f"choose-session:{self.tab_id}:{self.leaf_id}:work")
        self.assertIsNone(self.model.pane["agent"])
        self.assertEqual(self.sidebar.message, "Pane changed; choose again")


if __name__ == "__main__":
    unittest.main()
