import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tmux_workspaces.model import leaves
from tmux_workspaces.persistence import Store
from tmux_workspaces.sidebar import Sidebar


class AttachTargetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tw-attach-target-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root)
        self.addCleanup(self.store.close)
        self.model = self.store.load()
        self.model.split("right")
        self.store.save(self.model)
        self.tab_id = self.model.tab["id"]
        self.first, self.second = [pane["id"] for pane in leaves(self.model.tab["tree"])]
        self.focus = self.second
        self.display = Mock(sidebar="%0", small=False)
        self.display.panes = {self.first: "%1", self.second: "%2"}
        self.display.focused_leaf.side_effect = lambda: self.focus
        self.display.select.side_effect = lambda leaf: setattr(self, "focus", leaf)

        def render(tab, focus):
            self.focus = tab["focus"] if tab else None
            self.display.panes = {p["id"]: "%1" for p in leaves(tab["tree"])} if tab else {}

        self.display.render.side_effect = render
        screen = Mock()
        screen.getmaxyx.return_value = (38, 28)
        self.sidebar = Sidebar(
            screen,
            self.model,
            self.store,
            Mock(socket="/unused/source.sock", demo=None),
            self.display,
            Mock(),
        )

    def peer(self):
        peer = Store(self.root)
        self.addCleanup(peer.close)
        return peer, peer.load()

    def open_first(self):
        self.sidebar.action(f"attach-pane:{self.tab_id}:{self.first}")
        self.assertEqual(self.sidebar.menu, "agents")
        self.assertEqual(self.sidebar.attach_target[:2], (self.tab_id, self.first))

    def test_header_attachment_keeps_clicked_target_after_focus_moves(self):
        original_name = self.model.tab["name"]
        self.open_first()
        self.focus = self.second
        self.sidebar.attach("selected session")
        panes = {p["id"]: p for p in leaves(self.model.tab["tree"])}
        self.assertEqual(panes[self.first]["agent"], "selected session")
        self.assertIsNone(panes[self.second]["agent"])
        self.assertEqual(self.model.tab["focus"], self.first)
        self.assertEqual(self.model.tab["name"], original_name)
        self.display.shells.close.assert_not_called()

    def test_peer_deleting_target_cannot_redirect_attachment_to_surviving_pane(self):
        self.open_first()
        peer, model = self.peer()
        model.tab["focus"] = self.first
        model.close_pane()
        peer.save(model)
        self.sidebar.attach("must not attach")
        self.assertEqual(self.model.pane["id"], self.second)
        self.assertIsNone(self.model.pane["agent"])
        self.assertIsNone(self.sidebar.menu)
        self.assertIn("Pane changed", self.sidebar.message)
        self.display.tmux.run.assert_called_with("select-pane", "-t", "%0")
        self.display.shells.close.assert_not_called()

    def test_peer_attachment_change_is_not_overwritten_by_old_chooser(self):
        self.open_first()
        peer, model = self.peer()
        model.tab["focus"] = self.first
        model.attach("peer session", "/unused/peer.sock")
        peer.save(model)
        self.sidebar.attach("must not replace peer")
        target = next(p for p in leaves(self.model.tab["tree"]) if p["id"] == self.first)
        self.assertEqual(target["agent"], "peer session")
        self.assertEqual(target["source_socket"], "/unused/peer.sock")
        self.assertIn("Pane changed", self.sidebar.message)
        self.display.shells.close.assert_not_called()

    def test_stale_border_click_cannot_open_a_different_tab(self):
        before = copy.deepcopy(self.model.state)
        self.sidebar.action(f"attach-pane:000000000000:{self.first}")
        self.assertEqual(self.model.state, before)
        self.assertIsNone(self.sidebar.menu)
        self.display.select.assert_not_called()
        self.display.shells.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
