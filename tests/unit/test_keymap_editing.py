"""Keymap serialization and conflict-checked configuration writes."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from tmux_workspaces.keymap import (
    DEFAULT_KEYMAP,
    Keymap,
    KeymapConflict,
    KeymapError,
    KeymapFile,
    parse_keymap,
)


class SerializationTests(unittest.TestCase):
    def test_dictionary_round_trip_preserves_every_action(self):
        self.assertEqual(Keymap.from_dict(DEFAULT_KEYMAP.to_dict()), DEFAULT_KEYMAP)

    def test_cleared_binding_survives_a_reload(self):
        """The trap `to_dict` exists to avoid: an omitted action gets its default back."""
        cleared = Keymap.from_dict({"bindings": {"tab-options": []}})
        self.assertEqual(cleared.bindings["tab-options"], ())
        self.assertEqual(Keymap.from_dict(cleared.to_dict()).bindings["tab-options"], ())
        # Omitting the action instead is what would restore it, which is why
        # to_dict names them all.
        partial = {"prefix": cleared.prefix, "bindings": {}, "direct": {}}
        self.assertTrue(Keymap.from_dict(partial).bindings["tab-options"])


class KeymapFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tw-keymap-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "keymap.toml"

    def test_a_valid_file_round_trips_through_a_save(self):
        edited = Keymap.from_dict({"bindings": {"new-tab": ["F9"]}})
        KeymapFile(self.path).write(edited)
        self.assertEqual(parse_keymap(self.path.read_bytes()), edited)

    def test_a_generated_file_rewrites_without_loss(self):
        handle = KeymapFile(self.path)
        handle.write(DEFAULT_KEYMAP)
        self.assertTrue(handle.rewrites_cleanly(DEFAULT_KEYMAP.to_toml().encode()))

    def test_a_hand_written_file_is_reported_as_lossy(self):
        self.path.write_text("# my own notes\nprefix = 'C-b'\n")
        handle = KeymapFile(self.path)
        keymap = parse_keymap(handle.read_bytes()[0])
        self.assertFalse(handle.rewrites_cleanly(keymap.to_toml().encode()))

    def test_judging_the_rewrite_does_not_move_the_conflict_digest(self):
        """A second read here would let a save replace another writer's file."""
        self.path.write_text("# my own notes\nprefix = 'C-b'\n")
        handle = KeymapFile(self.path)
        keymap = parse_keymap(handle.read_bytes()[0])
        self.path.write_text("prefix = 'C-x'\n")
        handle.rewrites_cleanly(keymap.to_toml().encode())
        with self.assertRaises(KeymapConflict):
            handle.write(DEFAULT_KEYMAP)
        self.assertEqual(self.path.read_text(), "prefix = 'C-x'\n")

    def test_an_existing_file_nobody_read_is_never_replaced(self):
        before = "prefix = 'C-b'\n"
        self.path.write_text(before)
        with self.assertRaises(KeymapError) as caught:
            KeymapFile(self.path).write(DEFAULT_KEYMAP)
        self.assertIn("not read before saving", str(caught.exception))
        self.assertEqual(self.path.read_text(), before)

    def test_a_first_save_to_a_missing_path_still_works(self):
        KeymapFile(self.path).write(DEFAULT_KEYMAP)
        self.assertTrue(self.path.exists())

    def test_a_concurrent_edit_is_refused_and_the_other_writer_keeps_the_file(self):
        handle = KeymapFile(self.path)
        handle.write(DEFAULT_KEYMAP)
        handle.read_bytes()
        elsewhere = "prefix = 'C-b'\n"
        self.path.write_text(elsewhere)
        with self.assertRaises(KeymapConflict) as caught:
            handle.write(Keymap.from_dict({"bindings": {"new-tab": ["F9"]}}))
        self.assertIn("changed on disk", str(caught.exception))
        self.assertEqual(self.path.read_text(), elsewhere)

    def test_a_symbolic_link_is_never_replaced(self):
        real = self.root / "real.toml"
        real.write_text("prefix = 'C-b'\n")
        link = self.root / "link.toml"
        link.symlink_to(real)
        handle = KeymapFile(link)
        handle.read_bytes()
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
        handle.read_bytes()
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
