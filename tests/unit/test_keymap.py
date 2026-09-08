import tempfile
import tomllib
import unittest
from pathlib import Path

from tmux_workspaces.controls import ACTIONS, DIRECT_SHORTCUTS, SHORTCUTS
from tmux_workspaces.keymap import (
    ACTION_CODES,
    DEFAULT_KEYMAP,
    MAX_KEYMAP_BYTES,
    Keymap,
    canonical_ghostty_trigger,
    canonical_tmux_key,
    direct_sequence,
    load_keymap,
)


class KeymapTests(unittest.TestCase):
    def test_defaults_preserve_actions_aliases_and_wire_codes(self):
        self.assertEqual(DEFAULT_KEYMAP.prefix, "C-g")
        self.assertEqual(
            dict(DEFAULT_KEYMAP.prefix_items()),
            SHORTCUTS | {"c": "new-tab", "s": "sidebar", "d": "quit"},
        )
        for action, (trigger, label, code) in DIRECT_SHORTCUTS.items():
            self.assertEqual(DEFAULT_KEYMAP.direct[action], (trigger,))
            self.assertEqual(DEFAULT_KEYMAP.label(action, command=True), label)
            self.assertEqual(direct_sequence(action), f"\x1b[{code}~")
        self.assertEqual(set(ACTION_CODES), ACTIONS)
        self.assertEqual(len(set(ACTION_CODES.values())), len(ACTIONS))
        self.assertEqual(ACTION_CODES["quit"], 9019)
        self.assertEqual(ACTION_CODES["workspaces"], 9018)

    def test_override_replaces_aliases_and_unbinding_does_not_remove_other_actions(self):
        keymap = Keymap.from_dict(
            {"prefix": "C-b", "bindings": {"new-tab": ["C-t"], "close-tab": []}}
        )
        self.assertEqual(keymap.bindings["new-tab"], ("C-t",))
        self.assertNotIn("t", dict(keymap.prefix_items()))
        self.assertNotIn("c", dict(keymap.prefix_items()))
        self.assertNotIn("&", dict(keymap.prefix_items()))
        self.assertEqual(keymap.bindings["split-right"], DEFAULT_KEYMAP.bindings["split-right"])
        self.assertEqual(keymap.direct, DEFAULT_KEYMAP.direct)
        with self.assertRaises(TypeError):
            keymap.bindings["new-tab"] = ("bad",)

    def test_canonical_tmux_keys_preserve_case_and_normalize_control_aliases(self):
        for raw, expected in {
            "C-I": "Tab",
            "C-M": "Enter",
            "C-[": "Escape",
            "C-?": "BSpace",
            "C-@": "C-Space",
            "C-Space": "C-Space",
            "C-H": "C-h",
            "C-M-X": "M-C-x",
            "M-C-X": "M-C-x",
            "M-X": "M-X",
            "X": "X",
            "backspace": "BSpace",
            "return": "Enter",
            "esc": "Escape",
            "f2": "F2",
        }.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_tmux_key(raw), expected)
                self.assertEqual(canonical_tmux_key(expected), expected)

    def test_duplicate_normalized_keys_are_rejected_including_same_action(self):
        for bindings in (
            {"new-tab": ["t", "t"]},
            {"new-tab": ["Tab"], "attach": ["C-i"]},
            {"new-tab": ["Enter"], "attach": ["C-m"]},
            {"new-tab": ["M-C-t"], "attach": ["C-M-T"]},
            {"new-tab": ["a"]},
        ):
            with self.subTest(bindings=bindings), self.assertRaisesRegex(ValueError, "duplicate"):
                Keymap.from_dict({"bindings": bindings})
        for prefix, key in (("C-g", "C-G"), ("Tab", "C-I"), ("C-b", "Escape")):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ValueError, "reserved"):
                Keymap.from_dict({"prefix": prefix, "bindings": {"new-tab": [key]}})
        with self.assertRaisesRegex(ValueError, "cancellation"):
            Keymap.from_dict({"prefix": "C-["})

    def test_unknown_actions_tables_and_unsafe_keys_fail_with_context(self):
        for data, expected in (
            ({"unknown": 1}, "unknown keymap option"),
            ({"bindings": []}, "bindings"),
            ({"direct": "super+t"}, "direct"),
            ({"bindings": {"new-tab": "t"}}, "bindings.new-tab"),
            ({"bindings": {"mouse:left:1:2": ["t"]}}, "unknown action"),
            ({"bindings": {"attach-pane:abcdefabcdef:abcdefabcdef": ["t"]}}, "unknown action"),
            ({"bindings": {"new-tab": ["t"] * 33}}, "at most 32"),
            ({"prefix": True}, "prefix"),
        ):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, expected):
                Keymap.from_dict(data)
        for key in ("\n", "C-C-t", "C-1", "$(id)", "User1", "MouseDown1Pane", ";", "\\", "é", ""):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "bindings.new-tab"):
                Keymap.from_dict({"bindings": {"new-tab": [key]}})

    def test_direct_keys_require_safe_modified_triggers_and_reject_alias_duplicates(self):
        self.assertEqual(canonical_ghostty_trigger("shift+super+t"), "super+shift+t")
        for trigger in (
            "t",
            "shift+t",
            "super+super+t",
            "global:super+t",
            "super+t=quit",
            "super+t\nkeybind=super+w=quit",
            "cmd+t",
            "super+unknown",
            "super+T",
            "",
        ):
            with (
                self.subTest(trigger=trigger),
                self.assertRaisesRegex(ValueError, "direct.new-tab"),
            ):
                Keymap.from_dict({"direct": {"new-tab": [trigger]}})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Keymap.from_dict({"direct": {"new-tab": ["shift+super+w"]}})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Keymap.from_dict({"direct": {"new-tab": ["super+e", "super+e"]}})

    def test_direct_triggers_cannot_shadow_the_prefix_or_prefix_action_keys(self):
        for prefix, bindings, trigger in (
            ("C-a", {}, "ctrl+a"),
            ("C-a", {}, "shift+ctrl+a"),
            ("C-g", {"new-tab": ["M-t"]}, "alt+t"),
            ("C-g", {"new-tab": ["M-T"]}, "alt+shift+t"),
            ("C-g", {"new-tab": ["M-C-T"]}, "ctrl+alt+t"),
            ("C-g", {"new-tab": ["Tab"]}, "ctrl+i"),
            ("C-g", {"new-tab": ["M-Left"]}, "alt+left"),
            ("C-g", {"new-tab": ["C-Space"]}, "ctrl+space"),
            ("C-g", {"new-tab": ["C-Space"]}, "ctrl+shift+2"),
            ("C-g", {"new-tab": ["M-BTab"]}, "alt+shift+tab"),
            ("C-g", {"new-tab": ["M-{"]}, "alt+shift+bracket_left"),
            ("C-g", {}, "ctrl+bracket_left"),
        ):
            with self.subTest(trigger=trigger), self.assertRaisesRegex(ValueError, "shadows"):
                Keymap.from_dict(
                    {"prefix": prefix, "bindings": bindings, "direct": {"quit": [trigger]}}
                )
        # Prefix-table modifiers are significant, and Super is a separate layer.
        keymap = Keymap.from_dict(
            {"bindings": {"new-tab": ["M-T"]}, "direct": {"quit": ["alt+t", "super+ctrl+g"]}}
        )
        self.assertEqual(keymap.direct["quit"], ("alt+t", "super+ctrl+g"))
        Keymap.from_dict({"bindings": {"new-tab": []}, "direct": {"new-tab": ["alt+t"]}})

    def test_effective_help_and_profile_cover_remaps_unbindings_and_new_direct_actions(self):
        keymap = Keymap.from_dict(
            {
                "bindings": {"new-tab": ["F2"], "close-tab": []},
                "direct": {
                    "new-tab": ["ctrl+alt+t", "super+e"],
                    "close-pane": [],
                    "quit": ["super+t"],
                    "workspaces": ["super+f2"],
                },
            }
        )
        self.assertIn(("F2", "New tab"), keymap.prefix_help_rows())
        self.assertNotIn("Close tab", [row[1] for row in keymap.prefix_help_rows()])
        self.assertIn(("⌃⌥T / ⌘E", "New tab"), keymap.direct_help_rows())
        self.assertNotIn("Close pane", [row[1] for row in keymap.direct_help_rows()])
        profile = keymap.ghostty_bindings()
        self.assertIn("keybind = super+w=ignore\n", profile)
        self.assertNotIn("keybind = super+t=ignore", profile)
        self.assertIn("keybind = super+t=csi:9019~\n", profile)
        self.assertIn("keybind = super+f2=csi:9018~\n", profile)
        self.assertIn("keybind = ctrl+alt+t=csi:9001~\n", profile)
        self.assertEqual(Keymap.from_dict(tomllib.loads(keymap.to_toml())), keymap)

    def test_file_precedence_disable_relative_path_and_implicit_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "tmux-workspaces"
            config.mkdir(parents=True)
            (config / "keymap.toml").write_text('prefix = "C-a"\n')
            (root / "env.toml").write_text('prefix = "C-b"\n')
            (root / "explicit.toml").write_text('prefix = "C-e"\n')
            env = {"HOME": str(root), "XDG_CONFIG_HOME": str(root / "config")}
            self.assertEqual(load_keymap(environ=env).prefix, "C-a")
            env["TMUX_WORKSPACES_KEYMAP"] = "env.toml"
            self.assertEqual(load_keymap(environ=env, cwd=root).prefix, "C-b")
            self.assertEqual(load_keymap("explicit.toml", environ=env, cwd=root).prefix, "C-e")
            self.assertEqual(load_keymap(disabled=True, environ=env), DEFAULT_KEYMAP)
            with self.assertRaisesRegex(ValueError, "combined"):
                load_keymap("explicit.toml", disabled=True, environ=env, cwd=root)
            (config / "keymap.toml").unlink()
            self.assertEqual(load_keymap(environ={"HOME": str(root)}), DEFAULT_KEYMAP)
            self.assertEqual(load_keymap("~/explicit.toml", environ=env).prefix, "C-e")
            with self.assertRaisesRegex(ValueError, "file does not exist"):
                load_keymap("missing.toml", environ=env, cwd=root)
            with self.assertRaisesRegex(ValueError, "file does not exist"):
                load_keymap(environ={"TMUX_WORKSPACES_KEYMAP": "missing"}, cwd=root)

    def test_malformed_oversized_and_unreadable_files_report_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keymap.toml"
            for content in (
                b"[bindings",
                b"\xff",
                b"#" * (MAX_KEYMAP_BYTES + 1),
                b'prefix = "C-g"\nprefix = "C-b"',
                b'[direct]\nnew-tab = ["bad"]',
            ):
                path.write_bytes(content)
                with (
                    self.subTest(content=content[:20]),
                    self.assertRaisesRegex(ValueError, "keymap.toml"),
                ):
                    load_keymap(path)
            with self.assertRaisesRegex(ValueError, "keymap"):
                load_keymap(Path(directory))


if __name__ == "__main__":
    unittest.main()
