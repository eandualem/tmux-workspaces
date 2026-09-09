"""Editing a keymap: candidate construction, conflict reporting and safe saves.

These cover the operations an interactive editor performs before it draws
anything, so they need no terminal. `Keymap.from_dict` stays the authority on
what a keymap may contain; what is new here is naming the current owner of a key
instead of raising on the first problem, and replacing a file without losing the
one already there.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from tmux_workspaces.keymap import (
    ACTION_LABELS,
    DEFAULT_KEYMAP,
    MAX_KEYMAP_BYTES,
    Keymap,
    KeymapConflict,
    KeymapError,
    KeymapFile,
    claim_for,
)


class CandidateTests(unittest.TestCase):
    def test_dictionary_round_trip_preserves_every_action(self):
        self.assertEqual(Keymap.from_dict(DEFAULT_KEYMAP.to_dict()), DEFAULT_KEYMAP)

    def test_cleared_binding_survives_a_reload(self):
        """The trap `to_dict` exists to avoid: an omitted action gets its default back."""
        cleared = DEFAULT_KEYMAP.with_keys("bindings", "tab-options", [])
        self.assertEqual(cleared.bindings["tab-options"], ())
        self.assertEqual(Keymap.from_dict(cleared.to_dict()).bindings["tab-options"], ())
        # Omitting the action instead is what would restore it, which is why
        # to_dict names them all.
        partial = {"prefix": cleared.prefix, "bindings": {}, "direct": {}}
        self.assertTrue(Keymap.from_dict(partial).bindings["tab-options"])

    def test_assignment_and_unbinding(self):
        assigned = DEFAULT_KEYMAP.with_keys("bindings", "new-tab", ["F9"])
        self.assertEqual(assigned.bindings["new-tab"], ("F9",))
        self.assertEqual(assigned.with_keys("bindings", "new-tab", []).bindings["new-tab"], ())

    def test_restoring_an_action_default_needs_no_extra_table(self):
        edited = DEFAULT_KEYMAP.with_keys("bindings", "new-tab", ["F9"])
        restored = edited.with_keys("bindings", "new-tab", DEFAULT_KEYMAP.bindings["new-tab"])
        self.assertEqual(restored.bindings["new-tab"], DEFAULT_KEYMAP.bindings["new-tab"])

    def test_invalid_edits_never_become_a_keymap(self):
        for section, action, keys, expected in (
            ("bindings", "new-tab", ["C-g"], "reserved"),
            ("bindings", "new-tab", ["Escape"], "reserved"),
            ("bindings", "new-tab", ["not a key"], "new-tab"),
            ("bindings", "new-tab", ["v"], "duplicate"),
            ("direct", "new-tab", ["ctrl+g"], "shadows"),
        ):
            with self.subTest(keys=keys), self.assertRaises(ValueError) as caught:
                DEFAULT_KEYMAP.with_keys(section, action, keys)
            self.assertIn(expected, str(caught.exception))

    def test_unknown_section_and_action_are_refused(self):
        with self.assertRaises(ValueError):
            DEFAULT_KEYMAP.with_keys("elsewhere", "new-tab", ["F9"])
        with self.assertRaises(ValueError):
            DEFAULT_KEYMAP.with_keys("bindings", "no-such-action", ["F9"])

    def test_prefix_replacement(self):
        self.assertEqual(DEFAULT_KEYMAP.with_prefix("C-b").prefix, "C-b")
        with self.assertRaises(ValueError):
            DEFAULT_KEYMAP.with_prefix("Escape")

    def test_releasing_a_key_frees_it_for_another_action(self):
        held = next(action for action, keys in DEFAULT_KEYMAP.bindings.items() if "t" in keys)
        released = DEFAULT_KEYMAP.released("bindings", "t")
        self.assertNotIn("t", released.bindings[held])
        # Only then can it be assigned elsewhere, which is the confirmed
        # two-step the editor performs rather than silently stealing a key.
        taken = released.with_keys("bindings", "quit", ["t"])
        self.assertIn("t", taken.bindings["quit"])


class ClaimTests(unittest.TestCase):
    def test_a_free_key_has_no_claim(self):
        self.assertIsNone(claim_for(DEFAULT_KEYMAP, "bindings", "F9"))
        self.assertIsNone(claim_for(DEFAULT_KEYMAP, "direct", "super+shift+j"))

    def test_a_used_key_names_the_action_that_holds_it(self):
        claim = claim_for(DEFAULT_KEYMAP, "bindings", "t")
        self.assertEqual((claim.kind, claim.action, claim.held), ("bindings", "new-tab", "t"))
        self.assertIn(ACTION_LABELS["new-tab"], claim.reason)

    def test_reserved_keys_are_named_as_reservations(self):
        prefix = claim_for(DEFAULT_KEYMAP, "bindings", DEFAULT_KEYMAP.prefix)
        self.assertEqual((prefix.kind, prefix.action), ("prefix", None))
        cancel = claim_for(DEFAULT_KEYMAP, "bindings", "Escape")
        self.assertEqual((cancel.kind, cancel.action), ("cancel", None))

    def test_a_terminal_trigger_reports_the_prefix_it_would_shadow(self):
        claim = claim_for(DEFAULT_KEYMAP, "direct", "ctrl+g")
        self.assertEqual(claim.kind, "prefix")
        cancel = claim_for(DEFAULT_KEYMAP, "direct", "ctrl+bracket_left")
        self.assertEqual(cancel.kind, "cancel")

    def test_shadowing_is_reported_in_both_directions_with_the_removable_spelling(self):
        keymap = DEFAULT_KEYMAP.with_keys("direct", "new-tab", ["ctrl+t"])
        claim = claim_for(keymap, "bindings", "C-t")
        # The editor has to remove `ctrl+t`, not `C-t`: they are the same
        # physical key written two ways, and only one of them is in the file.
        self.assertEqual((claim.kind, claim.action, claim.held), ("direct", "new-tab", "ctrl+t"))
        self.assertIn("shadowed", claim.reason)

    def test_a_trigger_the_terminal_never_forwards_cannot_shadow(self):
        """Command triggers reach the viewer as a private sequence, not a key."""
        keymap = DEFAULT_KEYMAP.with_keys("bindings", "new-tab", ["F9"])
        self.assertIsNone(claim_for(keymap, "direct", "super+shift+f9"))


class KeymapFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tw-keymap-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "keymap.toml"

    def test_a_missing_file_reads_as_the_shipped_map(self):
        load = KeymapFile(self.path).read()
        self.assertEqual(load.keymap, DEFAULT_KEYMAP)
        self.assertIsNone(load.diagnostic)

    def test_a_valid_file_round_trips_through_a_save(self):
        edited = DEFAULT_KEYMAP.with_keys("bindings", "new-tab", ["F9"])
        KeymapFile(self.path).write(edited)
        self.assertEqual(KeymapFile(self.path).read().keymap, edited)

    def test_an_invalid_file_reports_a_repair_and_keeps_working(self):
        self.path.write_text("prefix = 'not a key'\n")
        load = KeymapFile(self.path).read()
        self.assertEqual(load.keymap, DEFAULT_KEYMAP)
        self.assertIn("saving will replace it", load.diagnostic)

    def test_an_oversized_file_promises_no_repair_it_cannot_perform(self):
        self.path.write_text("# padding\n" * (MAX_KEYMAP_BYTES // 5))
        load = KeymapFile(self.path).read()
        self.assertIn("shrink it", load.diagnostic)

    def test_a_generated_file_rewrites_without_loss(self):
        handle = KeymapFile(self.path)
        handle.write(DEFAULT_KEYMAP)
        self.assertTrue(handle.rewrites_cleanly(DEFAULT_KEYMAP))

    def test_a_hand_written_file_is_reported_as_lossy(self):
        self.path.write_text("# my own notes\nprefix = 'C-b'\n")
        handle = KeymapFile(self.path)
        keymap = handle.read().keymap
        self.assertFalse(handle.rewrites_cleanly(keymap))

    def test_a_concurrent_edit_is_refused_and_the_other_writer_keeps_the_file(self):
        handle = KeymapFile(self.path)
        handle.write(DEFAULT_KEYMAP)
        handle.read()
        elsewhere = "prefix = 'C-b'\n"
        self.path.write_text(elsewhere)
        with self.assertRaises(KeymapConflict) as caught:
            handle.write(DEFAULT_KEYMAP.with_keys("bindings", "new-tab", ["F9"]))
        self.assertIn("changed on disk", str(caught.exception))
        self.assertEqual(self.path.read_text(), elsewhere)

    def test_a_symbolic_link_is_never_replaced(self):
        real = self.root / "real.toml"
        real.write_text("prefix = 'C-b'\n")
        link = self.root / "link.toml"
        link.symlink_to(real)
        handle = KeymapFile(link)
        handle.read()
        with self.assertRaises(KeymapError) as caught:
            handle.write(DEFAULT_KEYMAP)
        self.assertIn("symbolic link", str(caught.exception))
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), "prefix = 'C-b'\n")

    def test_a_read_only_file_keeps_its_contents_and_says_so(self):
        before = "prefix = 'C-b'\n"
        self.path.write_text(before)
        os.chmod(self.path, stat.S_IRUSR)
        self.addCleanup(os.chmod, self.path, stat.S_IRUSR | stat.S_IWUSR)
        handle = KeymapFile(self.path)
        handle.read()
        self.assertFalse(handle.writable())
        with self.assertRaises(KeymapError) as caught:
            handle.write(DEFAULT_KEYMAP)
        self.assertIn("read-only", str(caught.exception))
        self.assertIn("Your shortcuts are unchanged", str(caught.exception))
        self.assertEqual(self.path.read_text(), before)

    def test_saving_leaves_no_temporary_file_behind(self):
        KeymapFile(self.path).write(DEFAULT_KEYMAP)
        stray = [item.name for item in self.root.iterdir() if item.name.startswith(".keymap-")]
        self.assertEqual(stray, [])


if __name__ == "__main__":
    unittest.main()
