import copy
import unittest
from unittest.mock import Mock, patch

from tmux_workspaces.controls import resize_action, valid_action
from tmux_workspaces.display import Display, DisplayState, PaneState
from tmux_workspaces.model import Model, leaves


class SplitDragTests(unittest.TestCase):
    def setUp(self):
        self.model = Model.initial()
        self.model.split("right")
        self.display = Display("/viewer", "/source", "%0", "/shells", "/actions")
        self.display.surface = "black"
        self.display.tmux = Mock()
        first, second = leaves(self.model.tab["tree"])
        self.display.panes = {first["id"]: "%1", second["id"]: "%4"}
        self.display._band_gutters = {"%5": True}
        self.display._blank_gutters = {"%2", "%3"}
        self.state = DisplayState(
            (160, 38),
            {
                "%0": PaneState(0, 0, 0, 0, 0, 28, 38),
                "%2": PaneState(0, 0, 0, 29, 0, 1, 38, 1),
                "%1": PaneState(1, 0, 0, 31, 0, 62, 38),
                "%5": PaneState(0, 0, 0, 94, 0, 1, 38, 1),
                "%4": PaneState(0, 0, 0, 96, 0, 62, 38),
                "%3": PaneState(0, 0, 0, 159, 0, 1, 38, 1),
            },
        )
        self.display.state = Mock(return_value=self.state)

    def test_coordinates_are_bounded_and_missing_border_coordinates_are_explicit(self):
        self.assertEqual(resize_action("resize:start:0:65535"), ("start", 0, 65535))
        self.assertEqual(resize_action("resize:end:-1:-1"), ("end", -1, -1))
        for value in (
            "resize:bad:1:2",
            "resize:move:-2:0",
            "resize:move:65536:0",
            "resize:move:1:2;kill-server",
            "resize:move:1:2\n",
        ):
            self.assertIsNone(resize_action(value))
            self.assertFalse(valid_action(value))

    def test_release_clears_capture_even_if_window_or_tab_disappeared(self):
        self.display.resize_split(self.model.tab, "start", 94, 3)
        self.assertIsNotNone(self.display._split_drag)
        self.assertFalse(self.display.resize_split(None, "end", -1, -1))
        self.assertIsNone(self.display._split_drag)
        self.display.tmux.run.assert_called_with("set-option", "-g", "@viewer_resizing", "0")

    def test_gesture_cannot_resize_another_tab_with_reused_pane_ids(self):
        self.display.resize_split(self.model.tab, "start", 94, 3)
        other = copy.deepcopy(self.model.tab)
        other["id"] = "other"
        self.display.tmux.reset_mock()
        self.assertFalse(self.display.resize_split(other, "move", 102, 3))
        self.display.tmux.run.assert_called_once_with("set-option", "-g", "@viewer_resizing", "0")
        self.assertIsNone(self.display._split_drag)

    def test_external_geometry_change_invalidates_the_drag(self):
        self.display.resize_split(self.model.tab, "start", 94, 3)
        self.state.panes["%1"] = PaneState(1, 0, 0, 31, 0, 60, 38)
        self.display.tmux.reset_mock()
        self.assertFalse(self.display.resize_split(self.model.tab, "move", 102, 3))
        self.display.tmux.run.assert_called_once_with("set-option", "-g", "@viewer_resizing", "0")
        self.assertIsNone(self.display._split_drag)

    def test_layout_failure_restores_saved_ratio_and_release_clears_capture(self):
        self.display.resize_split(self.model.tab, "start", 94, 3)
        before = self.model.tab["tree"]["ratio"]
        with (
            patch("tmux_workspaces.display.padded_layout.layout", side_effect=ValueError("small")),
            self.assertRaisesRegex(ValueError, "small"),
        ):
            self.display.resize_split(self.model.tab, "end", 102, 3)
        self.assertEqual(self.model.tab["tree"]["ratio"], before)
        self.assertIsNone(self.display._split_drag)

    def test_plain_theme_does_not_intercept_native_split_resize(self):
        self.display.surface = "default"
        self.assertFalse(self.display.resize_split(self.model.tab, "move", 102, 3))
        self.display.tmux.run.assert_not_called()
