import curses
import unittest
from unittest.mock import Mock, patch

from tmux_workspaces.keymap import ACTION_LABELS, DEFAULT_KEYMAP, Keymap
from tmux_workspaces.shortcut_reference import (
    PAIR_LABELS,
    ShortcutReference,
    layout_rows,
    reference_rows,
)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.screen = Mock()
        self.screen.getmaxyx.return_value = (24, 100)
        self.reference = ShortcutReference(self.screen, DEFAULT_KEYMAP)

    def test_all_effective_actions_and_custom_aliases_are_readable_at_both_widths(self):
        keymap = Keymap.from_dict(
            {"bindings": {"new-tab": ["F8", "F9"], "close-pane": []}, "direct": {"close-pane": []}}
        )
        rows = reference_rows(keymap)
        text = "\n".join(" ".join(row[:3]) for row in rows)
        self.assertIn("F8 / F9", text)
        self.assertNotIn("Close pane", text)
        for action, label in ACTION_LABELS.items():
            if action.startswith("select-") or not (
                keymap.bindings[action] or keymap.direct[action]
            ):
                continue
            # Paired actions read as one line; the range rows fold the numbers.
            pair = next((PAIR_LABELS[p] for p in PAIR_LABELS if action in p), None)
            self.assertTrue(label in text or (pair and pair in text), label)
        self.assertIn("Select tab 1–9", text)
        self.assertIn("⌘1 … ⌘9", text)
        for width in (36, 96):
            lines = layout_rows(rows, width)
            for segments in lines:
                for column, part, style in segments:
                    self.assertLessEqual(column + len(part), width, part)
                    self.assertIn(style, ("normal", "muted", "accent"))
        # Wide: three columns; narrow: the keys stacked under each label.
        wide = layout_rows(rows, 96)
        self.assertIn(
            [(2, "TABS", "muted"), (34, "after prefix", "muted"), (54, "Ghostty", "muted")], wide
        )
        self.assertIn(
            [(2, "New tab", "normal"), (34, "F8 / F9", "accent"), (54, "⌘T", "muted")], wide
        )
        narrow = layout_rows(rows, 36)
        self.assertIn([(2, "New tab", "normal")], narrow)
        self.assertIn([(4, "after prefix: F8 / F9", "accent")], narrow)

    def test_a_pair_with_one_side_unbound_shows_the_bound_action_alone(self):
        keymap = Keymap.from_dict(
            {"bindings": {"previous-tab": []}, "direct": {"previous-tab": []}}
        )
        labels = [row[0] for row in reference_rows(keymap)]
        self.assertIn("Next tab", labels)
        self.assertNotIn("Next / previous tab", labels)
        self.assertIn("Next / previous pane", labels)
        self.assertIn("Show / hide agents", labels)

    def test_the_title_pages_and_the_footer_names_the_file(self):
        self.screen.getmaxyx.return_value = (16, 100)
        reference = ShortcutReference(
            self.screen, DEFAULT_KEYMAP, path="/home/me/.config/tmux-workspaces/keymap.toml"
        )
        reference.draw()
        texts = [call.args[2] for call in self.screen.addstr.call_args_list]
        self.assertIn("SHORTCUTS", texts)
        self.assertTrue(any(text.startswith("read-only · 1/") for text in texts), texts)
        self.assertIn("…/tmux-workspaces/keymap.toml · prefix ^g", texts)
        self.assertIn("esc / F10 close", texts)
        reference.key(curses.KEY_NPAGE)
        reference.draw()
        texts = [call.args[2] for call in self.screen.addstr.call_args_list]
        self.assertTrue(any(text.startswith("read-only · 2/") for text in texts), texts)

    def test_paste_typing_and_action_sequences_cannot_edit_or_close(self):
        before = self.reference.keymap.to_dict()
        for char in "\x1b[200~\x03\x13\x07tPASTE\x1b[201~\x1b[9001~\r\n\x13abc":
            self.reference.feed(char)
        self.assertFalse(self.reference.done)
        self.assertEqual(self.reference.keymap.to_dict(), before)
        self.assertEqual(self.reference.offset, 0)

    def test_scroll_resize_and_mouse_close_keep_the_reference_read_only(self):
        self.reference.draw()
        self.reference.key(curses.KEY_NPAGE)
        self.assertGreater(self.reference.offset, 0)
        self.reference.key(curses.KEY_END)
        self.screen.getmaxyx.return_value = (12, 40)
        self.reference.draw()
        self.reference.key(curses.KEY_HOME)
        self.assertEqual(self.reference.offset, 0)
        # The close control sits at the right end of the footer row.
        with patch(
            "tmux_workspaces.shortcut_reference.curses.getmouse",
            return_value=(0, 30, 11, 0, curses.BUTTON1_PRESSED),
        ):
            self.reference.key(curses.KEY_MOUSE)
        self.assertTrue(self.reference.done)

    def test_decoded_keys_and_delayed_escape_stay_inside_an_active_paste(self):
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=10):
            for char in "\x1b[200~text\x1b":
                self.reference.feed(char)
            self.reference.feed(curses.KEY_F10)
            self.reference.feed(curses.KEY_F10)
            self.reference.feed("\x1b")
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=10.5):
            self.reference.idle()
        self.assertFalse(self.reference.done)
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=13):
            self.reference.idle()
            self.reference.feed("\x03")
            self.assertFalse(self.reference.done)
            self.reference.feed("\x1b")
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=14):
            self.reference.idle()
        self.assertTrue(self.reference.done)

    def test_small_terminal_clears_mouse_close_hit_and_escape_still_closes(self):
        self.reference.draw()
        self.screen.getmaxyx.return_value = (8, 30)
        self.reference.draw()
        self.assertIsNone(self.reference.close_hit)
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=10):
            self.reference.feed("\x1b")
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=11):
            self.reference.idle()
        self.assertTrue(self.reference.done)
