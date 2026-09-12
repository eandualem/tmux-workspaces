import copy
import unittest
from unittest.mock import Mock

from tmux_workspaces.display import Display, DisplayState, _LayoutTooSmall
from tmux_workspaces.model import Model


class RenderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.display = Display("/viewer", "/source", "%0", "/shells", "/actions")
        self.display.select_sidebar = Mock()
        self.wide = DisplayState((160, 38), {})
        self.narrow = DisplayState((72, 16), {})
        self.model = Model.initial()
        self.model.split("right")
        self.before = copy.deepcopy(self.model.tab)

    def incomplete(self, error):
        def fail(*args):
            self.display.panes = {"wrong-partial-leaf": "%1"}
            self.display.last_size = self.wide.size
            self.display._rendered_key = self.display._rendered_shape = "partial"
            self.display._rendered_geometry = "partial"
            self.display._snapshot = self.wide
            raise error

        return fail

    def assert_dirty(self):
        self.assertEqual(self.display.panes, {})
        self.assertEqual(self.display.last_size, (0, 0))
        self.assertIsNone(self.display._rendered_key)
        self.assertIsNone(self.display._rendered_shape)
        self.assertIsNone(self.display._rendered_geometry)
        self.assertIsNone(self.display._snapshot)
        self.assertEqual(self.model.tab, self.before)
        self.display.select_sidebar.assert_called()

    def test_changed_window_retries_geometry_failure_with_original_requested_focus(self):
        for error in (
            _LayoutTooSmall("too small"),
            RuntimeError("size or position no space for a new pane"),
        ):
            with self.subTest(error=error):
                self.setUp()
                self.display.state = Mock(side_effect=[self.wide, self.narrow])
                calls = []

                def render(tab, focus, state, calls=calls, error=error):
                    calls.append(state)
                    if len(calls) == 1:
                        self.incomplete(error)()
                    self.assert_dirty()
                    self.assertIs(state, self.narrow)
                    self.assertFalse(focus)
                    self.display.panes = {tab["focus"]: "%2"}
                    self.display.last_size = state.size

                self.display._render_once = render
                self.display.render(self.model.tab, False)
                self.assertEqual(calls, [self.wide, self.narrow])
                self.assertEqual(self.display.panes, {self.before["focus"]: "%2"})
                self.assertEqual(self.model.tab, self.before)

    def test_same_size_failure_is_propagated_and_left_dirty_for_next_poll(self):
        error = _LayoutTooSmall("too small")
        self.display.state = Mock(side_effect=[self.wide, self.wide])
        self.display._render_once = Mock(side_effect=self.incomplete(error))
        with self.assertRaises(_LayoutTooSmall) as caught:
            self.display.render(self.model.tab, False)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.display._render_once.call_count, 1)
        self.assert_dirty()

    def test_repeated_resize_never_retries_more_than_once(self):
        error = _LayoutTooSmall("still too small")
        self.display.state = Mock(side_effect=[self.wide, self.narrow])
        self.display._render_once = Mock(side_effect=self.incomplete(error))
        with self.assertRaises(_LayoutTooSmall):
            self.display.render(self.model.tab, False)
        self.assertEqual(self.display._render_once.call_count, 2)
        self.assertEqual(self.display.state.call_count, 2)
        self.assert_dirty()

    def test_unrelated_errors_do_not_retry_even_if_the_window_changed(self):
        for error in (
            RuntimeError("shell failed"),
            OSError("fork failed"),
            ValueError("bad config"),
        ):
            with self.subTest(error=error):
                self.setUp()
                self.display.state = Mock(side_effect=[self.wide, self.narrow])
                self.display._render_once = Mock(side_effect=self.incomplete(error))
                with self.assertRaises(type(error)) as caught:
                    self.display.render(self.model.tab, False)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.display._render_once.call_count, 1)
                self.assertEqual(self.display.state.call_count, 1)
                self.assert_dirty()

    def test_recovery_query_and_focus_errors_do_not_mask_the_render_failure(self):
        error = _LayoutTooSmall("original")
        self.display.state = Mock(side_effect=[self.wide, RuntimeError("query failed")])
        self.display.select_sidebar.side_effect = RuntimeError("focus failed")
        self.display._render_once = Mock(side_effect=self.incomplete(error))
        with self.assertRaises(_LayoutTooSmall) as caught:
            self.display.render(self.model.tab, False)
        self.assertIs(caught.exception, error)
        self.assert_dirty()

    def test_entry_query_failure_does_not_start_or_cleanup_a_render(self):
        self.display.panes = {"existing": "%1"}
        self.display.state = Mock(side_effect=RuntimeError("query failed"))
        self.display._render_once = Mock()
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            self.display.render(self.model.tab, False)
        self.display._render_once.assert_not_called()
        self.display.select_sidebar.assert_not_called()
        self.assertEqual(self.display.panes, {"existing": "%1"})
