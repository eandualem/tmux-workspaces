"""Named presets: the file key, the editor row, and the pane borders and chooser
that now follow the viewer's colors."""

from __future__ import annotations

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
    ROLES,
    Theme,
    ThemeError,
    canonical_panel,
    parse_theme,
    preset_theme,
    tmux_color,
)


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
        self.assertIn('preset = "default"', text)
        self.assertIn("[accent]", text)
        self.assertEqual(parse_theme(text.encode()), custom)

    def test_bright_grounds_round_trip_through_all_presets(self):
        for preset in PRESET_NAMES:
            for name in ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"):
                with self.subTest(preset=preset, color=name):
                    theme = Theme.from_dict(
                        {"preset": preset, "panel": "bright-" + name, "surface": "bright" + name}
                    )
                    self.assertEqual(theme.panel, "bright" + name)
                    self.assertEqual(parse_theme(theme.to_toml().encode()), theme)

    def test_panel_override_follows_inherited_roles_and_preserves_explicit_backgrounds(self):
        for preset in ("default", "paper", "mono"):
            with self.subTest(preset=preset):
                theme = Theme.from_dict(
                    {
                        "preset": preset,
                        "panel": "bright-blue",
                        "accent": {"foreground": "red"},
                        "muted": {
                            "background": list(preset_theme(preset).roles["muted"].background)
                        },
                    }
                )
                self.assertEqual(theme.roles["normal"].background, ("bright-blue",))
                self.assertEqual(theme.roles["accent"].background, ("bright-blue",))
                self.assertEqual(
                    theme.roles["muted"].background, preset_theme(preset).roles["muted"].background
                )
                restored = parse_theme(theme.to_toml().encode())
                self.assertEqual(restored, theme)
                again = restored.with_panel("red")
                self.assertEqual(again.roles["normal"].background, ("red",))
                self.assertEqual(again.roles["accent"].background, ("red",))
                self.assertEqual(again.roles["muted"], restored.roles["muted"])
                self.assertEqual(again.roles["outline"], restored.roles["outline"])
                self.assertEqual(parse_theme(again.to_toml().encode()), again)

    def test_surface_override_follows_outline_but_preserves_explicit_background(self):
        for preset in PRESET_NAMES:
            with self.subTest(preset=preset):
                theme = Theme.from_dict({"preset": preset, "surface": "brightgreen"})
                self.assertEqual(theme.roles["outline"].background, ("bright-green",))
                self.assertEqual(theme.roles["normal"], preset_theme(preset).roles["normal"])
                restored = parse_theme(theme.to_toml().encode())
                self.assertEqual(restored, theme)
                self.assertEqual(restored.with_surface("red").roles["outline"].background, ("red",))
                fixed = theme.with_role("outline", background=["bright-green"])
                self.assertEqual(
                    parse_theme(fixed.to_toml().encode())
                    .with_surface("red")
                    .roles["outline"]
                    .background,
                    ("bright-green",),
                )

    def test_returning_to_preset_grounds_restores_inherited_fallbacks(self):
        for preset in PRESET_NAMES:
            base = preset_theme(preset)
            restored = (
                base.with_panel("red")
                .with_surface("green")
                .with_panel(base.panel)
                .with_surface(base.surface)
            )
            self.assertEqual(restored, base)
            self.assertEqual(parse_theme(restored.to_toml().encode()), base)

    def test_explicit_background_equal_to_panel_is_not_reclassified_on_save(self):
        theme = Theme.from_dict(
            {"normal": {"background": list(DEFAULT_THEME.roles["normal"].background)}}
        )
        self.assertIsNone(theme.preset_name())
        restored = parse_theme(theme.to_toml().encode())
        changed = restored.with_panel("red")
        self.assertEqual(changed.roles["normal"], DEFAULT_THEME.roles["normal"])
        self.assertEqual(changed.roles["accent"].background, ("red",))

    def test_an_rgb_slot_never_takes_a_number_a_role_already_uses(self):
        theme = DEFAULT_THEME.with_role("muted", foreground=["16"])
        palette = theme.resolve(256)
        self.assertEqual(palette.entries["muted"][:2], (16, 18))
        self.assertNotIn(16, palette.rgb)
        self.assertEqual(palette.describe("muted"), "color 16 on #22252b")
        # Every remaining exact color still has a slot, from 17 upward.
        self.assertEqual(sorted(palette.rgb), [17, 18, 19, 20, 21, 22])

    def test_live_theme_releases_rgb_slots_for_indexed_colors(self):
        fake, writes = Curses(), []
        first = DEFAULT_THEME.resolve(256)
        first.install(fake, writes.append)
        self.assertIn(16, first.rgb)
        second = DEFAULT_THEME.with_role("muted", foreground=[16]).resolve(256)
        second.install(fake, writes.append, previous_rgb=first.rgb)
        self.assertTrue(writes[-1].startswith("\x1b]104;16\x1b\\"))
        self.assertEqual(second.installed("muted")[0], 16)
        third = preset_theme("plain").resolve(256)
        third.install(fake, writes.append, previous_rgb=second.rgb)
        for slot in set(second.rgb) - set(third.rgb):
            self.assertIn(f"\x1b]104;{slot}\x1b\\", writes[-1])
        writes.clear()
        with self.assertRaises(ThemeError):
            second.install(Curses(fail_on=2), writes.append, previous_rgb=first.rgb)
        self.assertEqual(writes, [], "failed installation changed the pane palette")

    def test_tmux_spelling_of_installed_colors(self):
        self.assertEqual(tmux_color(-1), "default")
        self.assertEqual(tmux_color(0), "colour0")
        self.assertEqual(tmux_color(110), "colour110")


class PanelColorTests(unittest.TestCase):
    def display(self):
        return Display(
            "/tmp/view.sock", "/tmp/source.sock", "%0", "/tmp/shells.sock", "/tmp/action.sock"
        )

    def test_grounds_set_before_setup_are_applied_by_setup(self):
        display = self.display()
        display.tmux = Mock()
        display.style_panel("#22252b", "#292c33", "#31343b")
        display.tmux.batch.assert_not_called()
        display.setup()
        options = {
            call.args[2]: call.args[3]
            for call in display.tmux.run.call_args_list
            if call.args[:2] == ("set-window-option", "-g")
        }
        # With a surface of its own the border glyphs vanish into it, so the
        # padding beside each pane reads as blank and no border is marked.
        self.assertEqual(options["pane-border-style"], "fg=#292c33,bg=#292c33")
        self.assertEqual(options["pane-active-border-style"], "fg=#292c33,bg=#292c33")
        # The sidebar's own cells are curses'; its ground is the surface, which
        # shows only where curses leaves a cell alone.
        display.tmux.batch.assert_called_once_with(
            [
                ["set-option", "-p", "-t", "%0", "window-style", "bg=#292c33"],
                ["set-option", "-p", "-t", "%0", "window-active-style", "bg=#292c33"],
            ]
        )
        self.assertTrue(display.padded)

    def test_the_panel_alone_makes_a_band_and_no_padding(self):
        display = self.display()
        display.tmux = Mock()
        display.style_panel("#22252b")
        display.setup()
        options = {
            call.args[2]: call.args[3]
            for call in display.tmux.run.call_args_list
            if call.args[:2] == ("set-window-option", "-g")
        }
        self.assertEqual(options["pane-border-style"], "fg=#22252b,bg=#22252b")
        self.assertFalse(display.padded)
        self.assertEqual(display.separator, "#22252b")

    def test_a_panel_set_after_setup_reaches_tmux_in_one_batch(self):
        display = self.display()
        display.tmux = Mock()
        display.setup()
        display.tmux.reset_mock()
        display._empty_panes = {"%3"}
        display.style_panel("default")
        display.tmux.batch.assert_called_once_with(
            [
                ["set-option", "-g", "@viewer_padded", "0"],
                ["set-window-option", "-g", "pane-border-style", "fg=default,bg=default"],
                ["set-window-option", "-g", "pane-active-border-style", "fg=default,bg=default"],
                ["set-option", "-p", "-t", "%0", "window-style", "default"],
                ["set-option", "-p", "-t", "%0", "window-active-style", "default"],
                ["set-option", "-p", "-t", "%3", "window-style", "default"],
                ["set-option", "-p", "-t", "%3", "window-active-style", "default"],
            ]
        )
        self.assertEqual(display.panel_color, "default")

    def test_empty_panes_join_the_panel_and_filled_panes_leave_it(self):
        display = self.display()
        display.panel_color = "#272c36"
        model = Model.initial()
        model.add_tab(empty=True)
        empty = model.pane
        model.add_tab("filled")
        filled = model.pane
        tab = {"id": "t", "focus": empty["id"], "tree": {**empty}}
        commands = display._identity_commands(tab, {empty["id"]: "%5"})
        self.assertIn(["set-option", "-p", "-t", "%5", "window-style", "bg=#272c36"], commands)
        self.assertEqual(display._empty_panes, {"%5"})
        tab = {"id": "t", "focus": filled["id"], "tree": {**filled}}
        commands = display._identity_commands(tab, {filled["id"]: "%5"})
        self.assertIn(["set-option", "-p", "-t", "%5", "window-style", "default"], commands)
        self.assertEqual(display._empty_panes, set())

    def test_panel_values_are_canonical_tmux_colors(self):
        self.assertEqual(canonical_panel("#1F2430"), "#1f2430")
        self.assertEqual(canonical_panel(235), "colour235")
        self.assertEqual(canonical_panel("235"), "colour235")
        self.assertEqual(canonical_panel("bright-blue"), "brightblue")
        self.assertEqual(canonical_panel(" Default "), "default")
        for bad in ("#12345", "rgb(1,2,3)", 256, True, "mauve"):
            with self.assertRaises(ValueError):
                canonical_panel(bad)
        theme = parse_theme(b'panel = "#1f2430"\n')
        self.assertEqual(theme.panel, "#1f2430")
        self.assertIsNone(theme.preset_name())
        self.assertEqual(parse_theme(theme.to_toml().encode()), theme)
        with self.assertRaises(ValueError):
            parse_theme(b'panel = "mauve"\n')


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
        self.assertEqual(styles["muted"], 4)
        self.assertEqual(styles["background"], 1)
        self.assertTrue(styles["title"] & Curses.A_BOLD)

    def test_respawned_chooser_resets_inherited_rgb_before_using_indexed_color(self):
        self.path.write_text('preset = "default"\n[muted]\nforeground = "16"\n')
        fake = Curses()
        fake.COLORS = 256
        emitted = []
        self.assertIsNotNone(install_theme(fake, self.path, 256, emitted.append))
        self.assertEqual(fake.pairs[4][0], 16)
        self.assertIn("\x1b]104;16\x1b\\", "".join(emitted))
        self.assertNotIn("\x1b]4;16;", "".join(emitted))

    def test_the_outer_terminal_bounds_the_chooser_palette(self):
        self.path.write_text('preset = "paper"\n')
        fake = Curses()
        fake.COLORS = 256
        install_theme(fake, self.path, 8)
        self.assertEqual(fake.pairs[2], preset_theme("paper").resolve(8).entries["active"][:2])

    def test_an_unreadable_theme_never_stops_the_chooser(self):
        self.path.write_text("[active]\nforeground = '#8ab4f'\n")
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
