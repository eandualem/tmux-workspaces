"""Interaction rules for the shortcut editor, without a terminal.

The editor holds interaction state only, so everything it decides -- what a key
press means, when to ask before taking a shortcut, what a refused save leaves
behind -- is testable here. Rendering and focus belong to the sidebar.
"""

import curses
import unittest

from tmux_workspaces.keymap import DEFAULT_KEYMAP, Keymap
from tmux_workspaces.shortcut_editor import ShortcutEditor, captured_key, failed


class Recorder:
    """A save that records, and optionally refuses the way a real one does."""

    def __init__(self, error=None):
        self.saved = []
        self.error = error

    def __call__(self, keymap):
        if self.error is not None:
            raise self.error
        self.saved.append(keymap)


def editor(keymap=None, save=None, **options):
    return ShortcutEditor(
        keymap or DEFAULT_KEYMAP,
        save or Recorder(),
        DEFAULT_KEYMAP,
        "/tmp/keymap.toml",
        **options,
    )


def row_for(edit, section, action):
    return next(index for index, target in enumerate(edit.targets) if target == (section, action))


def typed(edit, text):
    edit.edit()
    edit.field.value = text
    edit.commit()


class CapturedKeyTests(unittest.TestCase):
    def test_ordinary_and_control_keys_are_named_as_the_grammar_spells_them(self):
        for key, expected in (
            ("z", "z"),
            ("\x06", "C-f"),
            (" ", "Space"),
            ("\t", "Tab"),
            ("\r", "Enter"),
            ("\x7f", "BSpace"),
            ("\x00", "C-Space"),
        ):
            with self.subTest(key=key):
                self.assertEqual(captured_key(key), expected)

    def test_keypad_keys_are_named_from_the_terminal_description(self):
        self.assertEqual(captured_key(curses.KEY_UP), "Up")
        self.assertEqual(captured_key(curses.KEY_F0 + 5), "F5")

    def test_a_key_the_grammar_cannot_store_is_refused_not_mangled(self):
        for key in (-1, 9999, "", "two"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                captured_key(key)


class EditingTests(unittest.TestCase):
    def test_every_action_appears_in_both_sections(self):
        edit = editor()
        self.assertEqual(len(edit.targets), 2 * len(DEFAULT_KEYMAP.bindings))
        self.assertEqual(edit.target, ("bindings", "new-tab"))

    def test_typing_a_free_key_stages_it_and_previews_the_change(self):
        edit = editor()
        typed(edit, "F9")
        self.assertEqual(edit.keys("bindings", "new-tab"), ("F9",))
        self.assertTrue(edit.changed)
        self.assertEqual(edit.changes(), ["New tab (prefix): t, c → F9"])
        self.assertEqual(edit.message, "")

    def test_an_unusable_key_keeps_the_field_open_with_a_reason(self):
        edit = editor()
        edit.edit()
        edit.field.value = "not a key"
        self.assertFalse(edit.commit())
        self.assertIsNotNone(edit.field)
        self.assertTrue(failed(edit.message))
        self.assertFalse(edit.changed)

    def test_unbinding_and_restoring_one_action(self):
        edit = editor()
        edit.unbind()
        self.assertEqual(edit.keys("bindings", "new-tab"), ())
        edit.restore()
        self.assertEqual(edit.keys("bindings", "new-tab"), DEFAULT_KEYMAP.bindings["new-tab"])
        self.assertFalse(edit.changed)

    def test_cancel_discards_every_staged_change(self):
        edit = editor()
        typed(edit, "F9")
        edit.cancel()
        self.assertTrue(edit.closed)
        self.assertFalse(edit.changed)
        self.assertEqual(edit.draft, DEFAULT_KEYMAP)


class DismissTests(unittest.TestCase):
    """Taking the screen away is one step, where Escape would take two."""

    def staged(self, state):
        edit = editor()
        typed(edit, "F9")
        if state == "field":
            edit.edit()
        elif state == "capture":
            edit.capture()
        elif state == "pending":
            edit.edit()
            edit.field.value = "v"
            edit.commit()
            self.assertIsNotNone(edit.pending)
        return edit

    def test_dismiss_closes_from_every_sub_state_and_discards_the_draft(self):
        for state in (None, "field", "capture", "pending"):
            with self.subTest(state=state):
                edit = self.staged(state)
                edit.dismiss()
                self.assertTrue(edit.closed)
                self.assertFalse(edit.changed)
                self.assertIsNone(edit.field)
                self.assertFalse(edit.capturing)
                self.assertIsNone(edit.pending)

    def test_cancel_still_takes_two_steps_from_a_sub_state(self):
        """Escape closes the field first; the editor only leaves on the second."""
        for state in ("field", "capture", "pending"):
            with self.subTest(state=state):
                edit = self.staged(state)
                edit.cancel()
                self.assertFalse(edit.closed)
                edit.cancel()
                self.assertTrue(edit.closed)

    def test_dismiss_writes_nothing(self):
        recorder = Recorder()
        edit = editor(save=recorder)
        typed(edit, "F9")
        edit.dismiss()
        self.assertEqual(recorder.saved, [])


class ClaimResolutionTests(unittest.TestCase):
    def test_taking_a_used_key_is_asked_before_it_happens(self):
        edit = editor()
        typed(edit, "v")
        self.assertIsNotNone(edit.pending)
        self.assertIn("Take it from", edit.message)
        # Nothing moved while the question is open.
        self.assertEqual(edit.keys("bindings", "new-tab"), DEFAULT_KEYMAP.bindings["new-tab"])

    def test_declining_keeps_both_shortcuts_as_they_were(self):
        edit = editor()
        typed(edit, "v")
        edit.confirm(False)
        self.assertIsNone(edit.pending)
        self.assertFalse(edit.changed)
        self.assertEqual(edit.draft.bindings["split-right"], DEFAULT_KEYMAP.bindings["split-right"])

    def test_accepting_moves_the_key_and_leaves_the_owner_its_others(self):
        edit = editor()
        typed(edit, "v")
        edit.confirm(True)
        self.assertEqual(edit.keys("bindings", "new-tab"), ("v",))
        self.assertNotIn("v", edit.draft.bindings["split-right"])
        self.assertIn("%", edit.draft.bindings["split-right"])

    def test_a_key_held_by_the_other_half_is_released_where_it_actually_lives(self):
        """`ctrl+t` and `C-t` are one physical key spelled two ways."""
        keymap = DEFAULT_KEYMAP.with_keys("direct", "new-tab", ["ctrl+t"])
        edit = editor(keymap)
        edit.index = row_for(edit, "bindings", "quit")
        typed(edit, "C-t")
        self.assertIn("shadowed", edit.message)
        edit.confirm(True)
        self.assertEqual(edit.draft.bindings["quit"], ("C-t",))
        self.assertEqual(edit.draft.direct["new-tab"], ())

    def test_a_reserved_key_is_refused_rather_than_taken(self):
        edit = editor()
        edit.index = row_for(edit, "bindings", "quit")
        typed(edit, DEFAULT_KEYMAP.prefix)
        edit.confirm(True)
        self.assertTrue(failed(edit.message))
        self.assertFalse(edit.changed)

    def test_escape_answers_the_question_by_keeping_the_existing_shortcut(self):
        edit = editor()
        typed(edit, "v")
        edit.key("\x1b")
        self.assertIsNone(edit.pending)
        self.assertFalse(edit.changed)
        self.assertFalse(edit.closed)


class CaptureTests(unittest.TestCase):
    def test_capturing_names_the_key_that_was_pressed(self):
        edit = editor()
        edit.capture()
        self.assertTrue(edit.capturing)
        edit.key("\x06")
        self.assertFalse(edit.capturing)
        self.assertEqual(edit.keys("bindings", "new-tab"), ("C-f",))

    def test_capture_is_refused_for_terminal_shortcuts_and_says_why(self):
        edit = editor()
        edit.index = row_for(edit, "direct", "new-tab")
        edit.capture()
        self.assertFalse(edit.capturing)
        self.assertIn("chosen, not captured", edit.message)
        self.assertFalse(edit.changed)

    def test_escape_leaves_capture_without_binding_anything(self):
        edit = editor()
        edit.capture()
        edit.key("\x1b")
        self.assertFalse(edit.capturing)
        self.assertFalse(edit.changed)
        self.assertFalse(edit.closed)

    def test_a_captured_key_already_in_use_still_asks_first(self):
        edit = editor()
        edit.capture()
        edit.key("v")
        self.assertIsNotNone(edit.pending)
        self.assertFalse(edit.changed)


class SaveTests(unittest.TestCase):
    def test_applying_writes_once_and_closes(self):
        recorder = Recorder()
        edit = editor(save=recorder)
        typed(edit, "F9")
        edit.apply()
        self.assertEqual(len(recorder.saved), 1)
        self.assertEqual(recorder.saved[0].bindings["new-tab"], ("F9",))
        self.assertTrue(edit.saved)
        self.assertTrue(edit.closed)

    def test_a_refused_save_keeps_the_draft_and_the_editor_open(self):
        edit = editor(save=Recorder(error=ValueError("the keymap file is read-only")))
        typed(edit, "F9")
        edit.apply()
        self.assertFalse(edit.saved)
        self.assertFalse(edit.closed)
        self.assertTrue(failed(edit.message))
        self.assertEqual(edit.keys("bindings", "new-tab"), ("F9",))

    def test_saving_nothing_says_so_instead_of_writing(self):
        recorder = Recorder()
        edit = editor(save=recorder)
        edit.apply()
        self.assertEqual(recorder.saved, [])
        self.assertFalse(edit.closed)
        self.assertIn("No changes", edit.message)

    def test_a_read_only_destination_is_announced_before_anything_is_typed(self):
        edit = editor(writable=False)
        self.assertTrue(failed(edit.message))
        self.assertIn("read-only", edit.message)

    def test_the_effect_message_never_promises_a_reload(self):
        text = editor().effect().lower()
        self.assertIn("after this", text)
        for claim in ("reload", "immediately", "now active"):
            self.assertNotIn(claim, text)

    def test_what_is_saved_is_a_keymap_that_loads_back_unchanged(self):
        recorder = Recorder()
        edit = editor(save=recorder)
        typed(edit, "F9")
        edit.apply()
        saved = recorder.saved[0]
        self.assertEqual(Keymap.from_dict(saved.to_dict()), saved)


if __name__ == "__main__":
    unittest.main()
