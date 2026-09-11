"""Named presets: the file key, the editor row, and the pane borders and chooser
that now follow the viewer's colors."""

from __future__ import annotations

import curses
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tmux_workspaces import chooser as chooser_module
from tmux_workspaces.application import roster_args
from tmux_workspaces.chooser import Chooser, draw, install_theme, theme_styles
from tmux_workspaces.cli import parser
from tmux_workspaces.display import Display
from tmux_workspaces.model import Model
from tmux_workspaces.theme import (
    DEFAULT_THEME,
    PRESET_DETAILS,
    PRESET_NAMES,
    ROLE_LABELS,
    ROLES,
    Theme,
    parse_theme,
    preset_theme,
    tmux_color,
)
from tmux_workspaces.theme_editor import PRESET, ThemeEditor, fit_labels, hint


class FakeCursesError(Exception):
    pass


class Curses:
    """Enough of the curses surface to install pairs without a terminal."""

    error = FakeCursesError
    A_BOLD, A_DIM, A_REVERSE, A_STANDOUT, A_UNDERLINE = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12

    def __init__(self, *, fail_on=None):
        self.pairs = {}
        self._fail_on = fail_on

    def start_color(self):
        pass

    def use_default_colors(self):
        pass

    def init_pair(self, number, foreground, background):
        if self._fail_on == number:
            self._fail_on = None
            raise FakeCursesError("init_pair failed")
        self.pairs[number] = (foreground, background)

    def color_pair(self, number):
        return number


class PresetThemeTests(unittest.TestCase):
    def test_the_shipped_theme_is_the_first_preset(self):
        self.assertEqual(PRESET_NAMES[0], "default")
        self.assertEqual(DEFAULT_THEME, preset_theme("default"))
        self.assertEqual(DEFAULT_THEME.preset_name(), "default")
        self.assertEqual(set(PRESET_DETAILS), set(PRESET_NAMES))

    def test_every_preset_resolves_legibly_on_every_palette_size(self):
        for name in PRESET_NAMES:
            theme = preset_theme(name)
            self.assertEqual(theme.preset_name(), name)
            for colors in (8, 16, 256):
                palette = theme.resolve(colors)
                # No role fell back to the shipped one for being invisible.
                self.assertEqual(palette.fallbacks, (), (name, colors))
                for role in ROLES:
                    foreground, background, _ = palette.entries[role]
                    self.assertTrue(
                        foreground < 0 or foreground != background, (name, colors, role)
                    )

    def test_an_unknown_preset_names_the_choices(self):
        with self.assertRaises(ValueError) as caught:
            preset_theme("neon")
        self.assertIn("neon", str(caught.exception))
        for name in PRESET_NAMES:
            self.assertIn(name, str(caught.exception))
        with self.assertRaises(ValueError):
            Theme.from_dict({"preset": 3})

    def test_a_file_may_start_from_a_preset_and_override_a_role(self):
        theme = parse_theme(b'preset = "paper"\n[accent]\nforeground = "red"\n')
        self.assertEqual(theme.roles["normal"], preset_theme("paper").roles["normal"])
        self.assertEqual(theme.roles["accent"].foreground, ("red",))
        self.assertIsNone(theme.preset_name())
        # The name is matched loosely; the sections are not.
        self.assertEqual(parse_theme(b'preset = " Plain "\n').preset_name(), "plain")
        with self.assertRaises(ValueError) as caught:
            parse_theme(b"[presets]\n")
        self.assertIn("preset", str(caught.exception))

    def test_saving_a_preset_writes_its_name_and_custom_colors_write_tables(self):
        for name in PRESET_NAMES:
            text = preset_theme(name).to_toml()
            self.assertIn(f'preset = "{name}"', text)
            self.assertNotIn("[active]", text)
            self.assertEqual(parse_theme(text.encode()), preset_theme(name))
        custom = DEFAULT_THEME.with_role("accent", foreground=["red"])
        text = custom.to_toml()
        self.assertNotIn("preset =", text)
        self.assertIn("[accent]", text)
        self.assertEqual(parse_theme(text.encode()), custom)

    def test_tmux_spelling_of_installed_colors(self):
        self.assertEqual(tmux_color(-1), "default")
        self.assertEqual(tmux_color(0), "colour0")
        self.assertEqual(tmux_color(110), "colour110")


class PresetRowTests(unittest.TestCase):
    """The editor's last row cycles presets; every other row is unchanged."""

    def setUp(self):
        self.installed, self.saved = [], []
        self.editor = ThemeEditor(
            DEFAULT_THEME,
            self.saved.append,
            self.installed.append,
            DEFAULT_THEME,
            labels=ROLE_LABELS,
        )

    def goto_preset(self):
        self.editor.select(len(self.editor.targets) - 1)
        self.assertTrue(self.editor.on_preset)

    def test_the_preset_row_names_the_preset_or_custom(self):
        rows = self.editor.rows()
        self.assertEqual(rows[-1][:3], ("Preset", PRESET, "default"))
        self.assertEqual(len(rows), len(ROLES) * 3 + 1)
        self.editor.preview(DEFAULT_THEME.with_role("muted", foreground=["red"]))
        self.assertEqual(self.editor.rows()[-1][2], "custom")
        self.assertIn("Custom", self.editor.describe_preset())

    def test_enter_and_the_arrow_keys_cycle_presets_with_a_live_preview(self):
        self.goto_preset()
        self.editor.key("\n")
        self.assertIsNone(self.editor.field)
        self.assertEqual(self.editor.draft, preset_theme(PRESET_NAMES[1]))
        self.assertEqual(self.installed, [preset_theme(PRESET_NAMES[1])])
        self.assertEqual(self.editor.preset(), PRESET_NAMES[1])
        self.assertIn(PRESET_DETAILS[PRESET_NAMES[1]], self.editor.describe_preset())
        self.editor.key(curses.KEY_LEFT)
        self.assertEqual(self.editor.draft, DEFAULT_THEME)
        self.editor.key(curses.KEY_LEFT)
        self.assertEqual(self.editor.draft, preset_theme(PRESET_NAMES[-1]))
        self.editor.key(curses.KEY_RIGHT)
        self.assertEqual(self.editor.draft, DEFAULT_THEME)
        self.assertTrue(self.editor.changed is False)
        self.assertEqual(self.saved, [])

    def test_custom_colors_step_to_the_first_or_last_preset(self):
        custom = DEFAULT_THEME.with_role("muted", foreground=["red"])
        self.editor.preview(custom)
        self.goto_preset()
        self.editor.key(curses.KEY_LEFT)
        self.assertEqual(self.editor.preset(), PRESET_NAMES[-1])
        self.editor.preview(custom)
        self.editor.key(curses.KEY_RIGHT)
        self.assertEqual(self.editor.preset(), PRESET_NAMES[0])

    def test_arrows_on_a_role_row_do_not_change_colors(self):
        self.editor.key(curses.KEY_RIGHT)
        self.editor.key(curses.KEY_LEFT)
        self.assertEqual(self.installed, [])
        self.assertEqual(self.editor.draft, DEFAULT_THEME)

    def test_a_click_on_the_preset_row_advances_it_and_apply_saves_the_name(self):
        self.editor.edit(len(self.editor.targets) - 1)
        self.assertEqual(self.editor.preset(), PRESET_NAMES[1])
        self.editor.apply()
        self.assertEqual(self.saved, [preset_theme(PRESET_NAMES[1])])
        self.assertIn(f'preset = "{PRESET_NAMES[1]}"', self.saved[0].to_toml())
        self.assertTrue(self.editor.closed)

    def test_a_refused_preview_keeps_the_previous_preset(self):
        refusing = ThemeEditor(
            DEFAULT_THEME,
            self.saved.append,
            Mock(side_effect=OSError("no pairs")),
            DEFAULT_THEME,
        )
        refusing.select(len(refusing.targets) - 1)
        refusing.key("\n")
        self.assertIn("no pairs", refusing.message)
        self.assertFalse(refusing.installed)

    def test_labels_and_hints_cover_the_preset_row(self):
        rows = self.editor.rows()
        for width in range(8, 24):
            labels = fit_labels(rows, width)
            self.assertEqual(len(set(labels)), len(rows), width)
            self.assertEqual(labels[-1], "Preset")
        self.assertIn("preset", hint(40, preset=True))
        self.assertNotIn("preset", hint(40))
        self.assertLessEqual(len(hint(10, preset=True)), len(hint(40, preset=True)))


class BorderColorTests(unittest.TestCase):
    def display(self):
        return Display(
            "/tmp/view.sock", "/tmp/source.sock", "%0", "/tmp/shells.sock", "/tmp/action.sock"
        )

    def test_colors_set_before_setup_are_applied_by_setup(self):
        display = self.display()
        display.tmux = Mock()
        display.style_borders("colour254")
        display.tmux.batch.assert_not_called()
        display.setup()
        options = {
            call.args[2]: call.args[3]
            for call in display.tmux.run.call_args_list
            if call.args[:2] == ("set-window-option", "-g")
        }
        # A band, not a line: the glyphs take the same color as their ground,
        # and the focused pane's border is not marked.
        self.assertEqual(options["pane-border-style"], "fg=colour254,bg=colour254")
        self.assertEqual(options["pane-active-border-style"], "fg=colour254,bg=colour254")

    def test_colors_set_after_setup_reach_tmux_in_one_batch(self):
        display = self.display()
        display.tmux = Mock()
        display.setup()
        display.tmux.reset_mock()
        display.style_borders("default")
        display.tmux.batch.assert_called_once_with(
            [
                ["set-window-option", "-g", "pane-border-style", "fg=default,bg=default"],
                ["set-window-option", "-g", "pane-active-border-style", "fg=default,bg=default"],
            ]
        )
        self.assertEqual(display.border_color, "default")


class ChooserThemeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "theme.toml"

    def test_without_a_theme_the_chooser_draws_with_attributes(self):
        self.assertIsNone(install_theme(Curses(), None, 256))

    def test_a_theme_file_colors_the_chooser_like_the_sidebar(self):
        self.path.write_text('preset = "paper"\n')
        fake = Curses()
        fake.COLORS = 256
        styles = install_theme(fake, self.path, 256)
        palette = preset_theme("paper").resolve(256)
        self.assertEqual(fake.pairs[2], palette.entries["active"][:2])
        self.assertEqual(styles["selected"], 2)
        # Paper's muted role adds dim, so the style is pair 4 plus that bit.
        self.assertEqual(styles["muted"], 4 | Curses.A_DIM)
        self.assertEqual(styles["background"], 1)
        self.assertTrue(styles["title"] & Curses.A_BOLD)

    def test_the_outer_terminal_bounds_the_chooser_palette(self):
        self.path.write_text('preset = "paper"\n')
        fake = Curses()
        fake.COLORS = 256
        install_theme(fake, self.path, 8)
        self.assertEqual(fake.pairs[2], preset_theme("paper").resolve(8).entries["active"][:2])

    def test_an_unreadable_theme_never_stops_the_chooser(self):
        self.path.write_text("[active]\nforeground = '#8ab4f8'\n")
        fake = Curses()
        fake.COLORS = 256
        styles = install_theme(fake, self.path, 256)
        # An invalid file falls back to the shipped colors rather than none.
        self.assertIsNotNone(styles)
        self.assertEqual(fake.pairs[2], DEFAULT_THEME.resolve(256).entries["active"][:2])
        refusing = Curses(fail_on=1)
        refusing.COLORS = 256
        self.assertIsNone(install_theme(refusing, self.path, 256))

    def test_draw_uses_the_given_styles_and_caps_the_selection_bar(self):
        screen = Mock()
        screen.getmaxyx.return_value = (20, 120)
        curses_fake = SimpleNamespace(error=Exception, A_BOLD=1, A_DIM=2, A_REVERSE=4)
        chooser = Chooser("t", "l")
        chooser.update({"work": {"online": True, "state": "idle"}}, "")
        chooser.select(1)
        styles = {"title": 11, "muted": 12, "selected": 13, "notice": 14}
        draw(screen, chooser, curses_fake, styles)
        calls = {call.args[2].rstrip(): call.args[4] for call in screen.addnstr.call_args_list}
        self.assertEqual(calls[chooser_module.TITLE], 11)
        self.assertEqual(calls[chooser_module.LEAD], 12)
        self.assertEqual(calls["▸ work"], 13)
        self.assertEqual(calls["idle"], 13)
        bars = [call.args[2] for call in screen.addnstr.call_args_list if "▸" in call.args[2]]
        self.assertEqual(len(bars[0]), chooser_module.BAR_WIDTH)

    def test_theme_styles_read_the_installed_palette(self):
        fake = Curses()
        palette = DEFAULT_THEME.resolve(256)
        palette.install(fake)
        styles = theme_styles(palette, fake)
        self.assertEqual(styles["selected"], palette.style("active"))
        self.assertEqual(styles["title"], palette.style("accent") | Curses.A_BOLD)


class ChooserArgumentTests(unittest.TestCase):
    def test_the_chooser_receives_the_theme_and_the_terminal_palette(self):
        base = {
            "demo": True,
            "backbone": False,
            "instance_dir": "/i",
            "backbone_data_dir": "/b",
            "url": None,
            "theme": Path("/home/x/.config/tmux-workspaces/theme.toml"),
            "terminal_colors": 8,
        }
        self.assertEqual(
            roster_args(SimpleNamespace(**base)),
            (
                "--demo",
                "--instance-dir",
                "/i",
                "--theme",
                "/home/x/.config/tmux-workspaces/theme.toml",
                "--terminal-colors",
                "8",
            ),
        )
        plain = roster_args(SimpleNamespace(**{**base, "theme": None, "terminal_colors": None}))
        self.assertEqual(plain, ("--demo", "--instance-dir", "/i"))

    def test_the_leaf_command_parses_back_to_the_same_theme(self):
        display = Display(
            "/tmp/view.sock",
            "/tmp/source.sock",
            "%0",
            "/tmp/shells.sock",
            "/tmp/action.sock",
            roster_args=("--theme", "/tmp/t.toml", "--terminal-colors", "16"),
        )
        model = Model.initial()
        model.add_tab(empty=True)
        display._tab_id = model.tab["id"]
        args = parser().parse_args(shlex.split(display._leaf_command(model.pane))[2:])
        self.assertTrue(args.chooser)
        self.assertEqual(str(args.theme), "/tmp/t.toml")
        self.assertEqual(args.terminal_colors, 16)


if __name__ == "__main__":
    unittest.main()
