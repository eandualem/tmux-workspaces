import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from tmux_workspaces import theme as theme_module
from tmux_workspaces.theme import (
    ATTRIBUTES,
    COMBINATIONS,
    DEFAULT_THEME,
    MAX_THEME_BYTES,
    ROLES,
    Theme,
    ThemeConflict,
    ThemeError,
    ThemeFile,
    canonical_color,
    load_theme,
    parse_theme_state,
    preset_theme,
    theme_path,
)


class FakeCurses(Exception):
    pass


class Curses:
    """Enough of the curses surface to install pairs without a terminal."""

    error = FakeCurses
    A_BOLD, A_DIM, A_REVERSE, A_STANDOUT, A_UNDERLINE = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12

    def __init__(self, *, default_colors=True, fail_on=None, pair_limit=None):
        self.pairs = {}
        self.started = 0
        self.defaults = 0
        self._default_colors = default_colors
        self._fail_on = fail_on
        self._pair_limit = pair_limit

    def start_color(self):
        self.started += 1

    def use_default_colors(self):
        if not self._default_colors:
            raise FakeCurses("no default colors")
        self.defaults += 1

    def init_pair(self, number, foreground, background):
        if self._fail_on == number:
            # Fire once: the rejected value fails, the restored one is valid.
            self._fail_on = None
            raise FakeCurses("init_pair failed")
        if self._pair_limit is not None and number > self._pair_limit:
            raise FakeCurses("init_pair failed")
        self.pairs[number] = (foreground, background)

    def color_pair(self, number):
        return number


def roles(pairs):
    """The role pairs alone: a rollback restores roles, not the derived combinations."""
    return {number: pair for number, pair in pairs.items() if number <= len(ROLES)}


class ColorValueTests(unittest.TestCase):
    def test_names_indexes_and_case_are_canonicalized(self):
        for raw, expected in {
            "default": "default",
            "Red": "red",
            "BRIGHT-CYAN": "bright-cyan",
            "bright_cyan": "bright-cyan",
            " white ": "white",
            238: "238",
            "238": "238",
            0: "0",
            255: "255",
        }.items():
            self.assertEqual(canonical_color(raw), expected, raw)

    def test_rgb_is_accepted_exactly_and_other_spellings_are_rejected(self):
        self.assertEqual(canonical_color(" #8AB4F8 "), "#8ab4f8")
        for raw in ("#fff", "0x11", "rgb(1,2,3)", "#8ab4f"):
            with self.assertRaises(ValueError) as caught:
                canonical_color(raw)
            self.assertIn("not a color", str(caught.exception))

    def test_out_of_range_unknown_and_wrong_types_are_errors(self):
        for raw in (300, -1, "puce", "", True, None, 3.5, "1" * 40):
            with self.assertRaises(ValueError):
                canonical_color(raw)


class ShippedAppearanceTests(unittest.TestCase):
    def test_defaults_reproduce_todays_pairs_exactly(self):
        palette = DEFAULT_THEME.resolve(256)
        # The approved palette: the panel and surface are tmux's grounds; the
        # roles are exact colors in the pane's own palette slots, on the panel.
        self.assertEqual((DEFAULT_THEME.panel, DEFAULT_THEME.surface), ("#15171c", "#1b1e24"))
        self.assertEqual(palette.entries["normal"][:2], (16, 17))
        self.assertEqual(palette.entries["active"][:2], (18, 19))
        self.assertEqual(palette.entries["accent"][:2], (20, 17))
        self.assertEqual(palette.entries["muted"][:2], (21, 17))
        self.assertEqual(palette.entries["outline"][:2], (22, 17))
        self.assertEqual(palette.entries["header"][:2], (16, 23))
        self.assertEqual(palette.entries["danger"][:2], (24, 17))
        self.assertEqual(
            dict(palette.rgb),
            {
                16: "#d4d6db",
                17: "#15171c",
                18: "#ffffff",
                19: "#262a33",
                20: "#e0a458",
                21: "#7d828c",
                22: "#2a2e36",
                23: "#1e2128",
                24: "#e06c75",
            },
        )

    def test_defaults_reproduce_todays_basic_palette_exactly(self):
        # White on blue, yellow, red and white: the deliberate basic choices.
        # Nearest-color approximation would pick black for 238, so the ordered
        # fallbacks are load-bearing.
        for colors in (8, 16):
            palette = DEFAULT_THEME.resolve(colors)
            self.assertEqual(palette.entries["normal"][:2], (7, 0), colors)
            self.assertEqual(palette.entries["active"][:2], (7, 4), colors)
            self.assertEqual(palette.entries["accent"][:2], (3, 0), colors)
            self.assertEqual(palette.entries["muted"][:2], (7, 0), colors)
            self.assertEqual(palette.entries["outline"][:2], (7, 0), colors)
            self.assertEqual(palette.entries["header"][:2], (7, 0), colors)
            self.assertEqual(palette.entries["danger"][:2], (1, 0), colors)
            self.assertEqual(dict(palette.rgb), {}, colors)
        # An 88-color pane cannot hold the exact colors, nor these 256-color
        # fallbacks, so the basic choices serve.
        palette = DEFAULT_THEME.resolve(88)
        self.assertEqual(palette.entries["accent"][:2], (3, 0))
        self.assertEqual(palette.entries["active"][:2], (7, 4))

    def test_pair_numbers_match_the_existing_sidebar_call_sites(self):
        palette = DEFAULT_THEME.resolve(256)
        self.assertEqual(
            ROLES, ("normal", "active", "accent", "muted", "outline", "header", "danger")
        )
        self.assertEqual([palette.pair(role) for role in ROLES], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([palette.pair(text, on) for text, on in COMBINATIONS], [8, 9, 10, 11, 12])

    def test_roles_are_immutable(self):
        with self.assertRaises(TypeError):
            DEFAULT_THEME.roles["normal"] = None


class ResolutionTests(unittest.TestCase):
    def test_first_supported_entry_of_an_ordered_list_wins(self):
        theme = Theme.from_dict({"accent": {"foreground": [240, 33, "red"]}})
        self.assertEqual(theme.resolve(256).entries["accent"][0], 240)
        self.assertEqual(theme.resolve(64).entries["accent"][0], 33)
        self.assertEqual(theme.resolve(8).entries["accent"][0], 1)

    def test_a_scalar_is_shorthand_for_a_single_entry_list(self):
        theme = Theme.from_dict({"accent": {"foreground": "red"}})
        self.assertEqual(theme.roles["accent"].foreground, ("red",))

    def test_exhausted_lists_fall_back_to_the_nearest_supported_color(self):
        theme = Theme.from_dict({"accent": {"foreground": [21]}})
        self.assertEqual(theme.resolve(256).entries["accent"][0], 21)
        self.assertEqual(theme.resolve(16).entries["accent"][0], 4)
        self.assertEqual(theme.resolve(8).entries["accent"][0], 4)

    def test_bright_colors_drop_to_their_base_with_bold_on_eight_color_terminals(self):
        theme = Theme.from_dict({"accent": {"foreground": "bright-red"}})
        self.assertEqual(theme.resolve(16).entries["accent"][:2], (9, 0))
        foreground, _background, attributes = theme.resolve(8).entries["accent"]
        self.assertEqual(foreground, 1)
        self.assertIn("bold", attributes)

    def test_resolution_never_raises_and_respects_the_count_it_is_given(self):
        # Below the startup floor everything falls to the terminal default. The
        # sidebar refuses to go there; resolution reports what it was asked.
        theme = Theme.from_dict({"accent": {"foreground": 200}})
        palette = theme.resolve(0)
        self.assertEqual(palette.entries["accent"][0], -1)
        self.assertEqual(theme.resolve(8).entries["accent"][0], 5)

    def test_identical_colors_fall_back_to_the_shipped_role_not_reverse(self):
        # Reverse swaps foreground and background, so on identical colors it
        # yields the same invisible pair. The repair must be a real fallback.
        theme = Theme.from_dict({"active": {"foreground": "red", "background": "red"}})
        palette = theme.resolve(256)
        self.assertEqual(palette.entries["active"][:2], (18, 19))
        self.assertNotIn("reverse", palette.entries["active"][2])

    def test_a_collision_created_only_by_a_reduced_palette_is_repaired_too(self):
        theme = Theme.from_dict(
            {"active": {"foreground": [231, "white"], "background": [238, "white"]}}
        )
        self.assertEqual(theme.resolve(256).entries["active"][:2], (231, 238))
        self.assertEqual(theme.resolve(8).entries["active"][:2], (7, 4))

    def test_terminal_defaults_on_both_sides_are_not_treated_as_a_collision(self):
        palette = preset_theme("forest").resolve(256)
        self.assertEqual(palette.entries["normal"][:2], (-1, -1))

    def test_attributes_are_preserved_and_ordered(self):
        theme = Theme.from_dict({"muted": {"attributes": ["underline", "bold", "bold"]}})
        self.assertEqual(theme.roles["muted"].attributes, ("bold", "underline"))
        self.assertEqual(theme.resolve(256).entries["muted"][2], ("bold", "underline"))


class ParsingTests(unittest.TestCase):
    def test_omitted_roles_and_fields_keep_their_shipped_values(self):
        theme = Theme.from_dict({"accent": {"foreground": "red"}})
        self.assertEqual(theme.roles["active"], DEFAULT_THEME.roles["active"])
        # A table naming one key changes one key; the rest of the role stays.
        self.assertEqual(theme.roles["accent"].background, ("#15171c", "233", "black"))
        self.assertEqual((theme.panel, theme.surface), (DEFAULT_THEME.panel, DEFAULT_THEME.surface))
        self.assertEqual(Theme.from_dict({}), DEFAULT_THEME)

    def test_invalid_configurations_are_rejected_with_located_messages(self):
        for data, fragment in (
            ({"nope": {}}, "unknown theme section"),
            ({"active": {"colour": "red"}}, "unknown option"),
            ({"active": "red"}, "expected a table"),
            ({"active": {"foreground": []}}, "foreground"),
            ({"active": {"foreground": ["red"] * 9}}, "foreground"),
            ({"active": {"foreground": "#fff"}}, "not a color"),
            ({"active": {"attributes": ["blink"]}}, "unknown attribute"),
            ({"active": {"attributes": "bold"}}, "attributes"),
            ("not a table", "must be a TOML table"),
        ):
            with self.assertRaises(ValueError, msg=data) as caught:
                Theme.from_dict(data)
            self.assertIn(fragment, str(caught.exception))

    def test_to_toml_round_trips_through_the_parser(self):
        theme = Theme.from_dict(
            {"active": {"foreground": [231, "white"], "background": "blue", "attributes": ["bold"]}}
        )
        self.assertEqual(Theme.from_dict(tomllib.loads(theme.to_toml())), theme)
        self.assertEqual(Theme.from_dict(tomllib.loads(DEFAULT_THEME.to_toml())), DEFAULT_THEME)


class PathTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))

    def test_precedence_is_explicit_then_environment_then_xdg_then_home(self):
        env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "TMUX_WORKSPACES_THEME": str(self.home / "from-env.toml"),
        }
        self.assertEqual(
            theme_path(self.home / "explicit.toml", environ=env), self.home / "explicit.toml"
        )
        self.assertEqual(theme_path(environ=env), self.home / "from-env.toml")
        without_env = {key: value for key, value in env.items() if key != "TMUX_WORKSPACES_THEME"}
        self.assertEqual(
            theme_path(environ=without_env),
            self.home / "xdg" / "tmux-workspaces" / "theme.toml",
        )
        self.assertEqual(
            theme_path(environ={"HOME": str(self.home)}),
            self.home / ".config" / "tmux-workspaces" / "theme.toml",
        )

    def test_relative_and_tilde_paths_resolve_against_cwd_and_the_given_home(self):
        env = {"HOME": str(self.home)}
        self.assertEqual(theme_path("~/colors.toml", environ=env), self.home / "colors.toml")
        self.assertEqual(
            theme_path("colors.toml", environ=env, cwd=self.home / "work"),
            self.home / "work" / "colors.toml",
        )

    def test_resolution_is_pure_and_does_not_touch_the_filesystem(self):
        target = theme_path(environ={"HOME": str(self.home)})
        self.assertTrue(target.is_absolute())
        self.assertFalse(target.exists())
        self.assertFalse((self.home / ".config").exists())


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.path = self.directory / "theme.toml"

    def test_a_missing_file_is_silently_the_shipped_theme(self):
        result = load_theme(self.path)
        self.assertEqual(result.theme, DEFAULT_THEME)
        self.assertIsNone(result.diagnostic)
        self.assertEqual(result.path, self.path)

    def test_a_valid_file_is_applied(self):
        self.path.write_text('[accent]\nforeground = "red"\n')
        self.assertEqual(load_theme(self.path).theme.roles["accent"].foreground, ("red",))

    def test_invalid_content_degrades_with_a_diagnostic_and_never_raises(self):
        for content in ("not toml ===", '[accent]\nforeground = "#fff"\n', "[nope]\n"):
            self.path.write_text(content)
            result = load_theme(self.path)
            self.assertEqual(result.theme, DEFAULT_THEME)
            self.assertIsNotNone(result.diagnostic)
            self.assertIn(str(self.path), result.diagnostic)

    def test_invalid_content_keeps_the_working_theme_rather_than_the_shipped_one(self):
        working = Theme.from_dict({"accent": {"foreground": "red"}})
        self.path.write_text("garbage ===")
        result = load_theme(self.path, working=working)
        self.assertEqual(result.theme, working)
        self.assertIsNotNone(result.diagnostic)

    def test_an_oversize_file_is_refused_without_reading_it_all(self):
        self.path.write_text("# padding\n" * MAX_THEME_BYTES)
        result = load_theme(self.path)
        self.assertEqual(result.theme, DEFAULT_THEME)
        self.assertIn("exceeds", result.diagnostic)

    @unittest.skipIf(os.geteuid() == 0, "root ignores file permissions")
    def test_an_unreadable_file_degrades_with_a_diagnostic(self):
        self.path.write_text('[accent]\nforeground = "red"\n')
        self.path.chmod(0o000)
        self.addCleanup(self.path.chmod, 0o600)
        result = load_theme(self.path)
        self.assertEqual(result.theme, DEFAULT_THEME)
        self.assertIsNotNone(result.diagnostic)


class ThemeFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.path = self.directory / "theme.toml"

    def test_write_creates_the_file_atomically_and_leaves_no_temporary(self):
        edited = Theme.from_dict({"accent": {"foreground": "red"}})
        ThemeFile(self.path).write(edited)
        self.assertEqual(load_theme(self.path).theme, edited)
        names = sorted(p.name for p in self.directory.iterdir())
        # The sibling lock file is deliberate and stable; no temporary may survive.
        self.assertEqual(names, [".theme.toml.lock", "theme.toml"])
        self.assertFalse([n for n in names if n.startswith(".theme-")])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_a_concurrent_edit_is_refused_and_the_working_theme_survives(self):
        self.path.write_text('[accent]\nforeground = "red"\n')
        handle = ThemeFile(self.path)
        handle.read_bytes()
        self.path.write_text('[accent]\nforeground = "green"\n')
        edited = Theme.from_dict({"accent": {"foreground": "red"}, "muted": {"foreground": "blue"}})
        with self.assertRaises(ThemeConflict) as caught:
            handle.write(edited)
        self.assertIn("changed on disk", str(caught.exception))
        self.assertEqual(edited.roles["muted"].foreground, ("blue",))
        self.assertEqual(load_theme(self.path).theme.roles["accent"].foreground, ("green",))

    def test_an_unchanged_invalid_file_can_be_repaired_by_an_explicit_apply(self):
        # Reading bytes retains their digest independently of parsing, so an
        # explicit repair can replace invalid content without losing another edit.
        self.path.write_text("this is not toml ===")
        handle = ThemeFile(self.path)
        self.assertEqual(handle.read_bytes(), (b"this is not toml ===", None))
        handle.write(DEFAULT_THEME)
        self.assertEqual(load_theme(self.path).theme, DEFAULT_THEME)

    def test_writing_a_file_that_appeared_after_a_missing_read_is_a_conflict(self):
        handle = ThemeFile(self.path)
        handle.read_bytes()
        self.path.write_text('[accent]\nforeground = "green"\n')
        with self.assertRaises(ThemeConflict):
            handle.write(DEFAULT_THEME)

    def test_a_symlink_target_is_refused_rather_than_replaced(self):
        real = self.directory / "real.toml"
        real.write_text('[accent]\nforeground = "red"\n')
        link = self.directory / "link.toml"
        link.symlink_to(real)
        with self.assertRaises(ThemeError) as caught:
            ThemeFile(link).write(DEFAULT_THEME)
        self.assertIn("symbolic link", str(caught.exception))
        self.assertTrue(link.is_symlink())
        self.assertEqual(load_theme(real).theme.roles["accent"].foreground, ("red",))

    def test_a_non_regular_target_is_refused(self):
        directory = self.directory / "adirectory"
        directory.mkdir()
        with self.assertRaises(ThemeError) as caught:
            ThemeFile(directory).write(DEFAULT_THEME)
        self.assertIn("regular file", str(caught.exception))

    @unittest.skipIf(os.geteuid() == 0, "root ignores file permissions")
    def test_a_file_that_could_not_be_read_is_never_replaced(self):
        """Three shapes: the guard must catch the middle one without over-reaching."""
        original = '[accent]\nforeground = "red"\n'
        for name, permission in (("stays-unreadable", 0o200), ("becomes-writable", 0o600)):
            path = self.directory / f"{name}.toml"
            path.write_text(original)
            os.chmod(path, 0o200)
            colors = ThemeFile(path)
            self.assertIsNotNone(colors.read_bytes()[1])
            os.chmod(path, permission)
            with self.assertRaises(ThemeError) as caught:
                colors.write(DEFAULT_THEME)
            self.assertIn("could not be read", str(caught.exception))
            os.chmod(path, 0o600)
            self.assertEqual(path.read_text(), original)

        # A readable file that changed underneath is a conflict, not this rule,
        # and a file never read at all still saves.
        readable = self.directory / "readable.toml"
        readable.write_text(original)
        colors = ThemeFile(readable)
        colors.read_bytes()
        readable.write_text('[muted]\nforeground = "green"\n')
        with self.assertRaises(ThemeConflict):
            colors.write(DEFAULT_THEME)
        fresh = self.directory / "fresh.toml"
        ThemeFile(fresh).write(DEFAULT_THEME)
        self.assertEqual(load_theme(fresh).theme, DEFAULT_THEME)

    @unittest.skipIf(os.geteuid() == 0, "root ignores file permissions")
    def test_a_read_only_file_is_refused_with_an_actionable_message(self):
        self.path.write_text('[accent]\nforeground = "red"\n')
        self.path.chmod(0o400)
        self.addCleanup(self.path.chmod, 0o600)
        handle = ThemeFile(self.path)
        handle.read_bytes()
        with self.assertRaises(ThemeError) as caught:
            handle.write(DEFAULT_THEME)
        self.assertIn("read-only", str(caught.exception))
        self.assertEqual(load_theme(self.path).theme.roles["accent"].foreground, ("red",))

    def test_a_second_write_after_a_successful_one_is_not_a_conflict(self):
        handle = ThemeFile(self.path)
        handle.read_bytes()
        handle.write(DEFAULT_THEME)
        handle.write(Theme.from_dict({"accent": {"foreground": "red"}}))
        self.assertEqual(load_theme(self.path).theme.roles["accent"].foreground, ("red",))

    def test_the_parent_directory_is_created_privately(self):
        nested = self.directory / "config" / "tmux-workspaces" / "theme.toml"
        ThemeFile(nested).write(DEFAULT_THEME)
        self.assertTrue(nested.exists())
        self.assertEqual(nested.parent.stat().st_mode & 0o777, 0o700)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.path = self.directory / "theme.toml"

    def _themes(self, count):
        colors = ("red", "green", "blue", "yellow", "magenta", "cyan")
        return [Theme.from_dict({"accent": {"foreground": [colors[n]]}}) for n in range(count)]

    def test_concurrent_writers_never_leave_a_torn_or_unparsable_file(self):
        import threading

        self.path.write_text('[accent]\nforeground = "white"\n')
        themes = self._themes(6)
        handles = []
        for _ in themes:
            handle = ThemeFile(self.path)
            handle.read_bytes()
            handles.append(handle)
        errors, done = [], []
        start = threading.Barrier(len(themes))

        def attempt(handle, theme):
            start.wait()
            try:
                handle.write(theme)
                done.append(theme)
            except ThemeError as error:
                errors.append(error)

        threads = [
            threading.Thread(target=attempt, args=pair)
            for pair in zip(handles, themes, strict=True)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse([t for t in threads if t.is_alive()], "a writer blocked")
        # Whoever won, the file must be complete and valid: no interleaved bytes.
        result = load_theme(self.path)
        self.assertIsNone(result.diagnostic)
        self.assertIn(result.theme, themes)
        self.assertEqual(len(done) + len(errors), len(themes))
        self.assertTrue(done)
        # Every loser is told its edit was not applied, never silently dropped.
        for error in errors:
            self.assertIn("unchanged", str(error))

    def test_simultaneous_first_saves_to_a_missing_file_do_not_both_claim_success(self):
        import threading

        themes = self._themes(4)
        handles = []
        for _ in themes:
            handle = ThemeFile(self.path)
            handle.read_bytes()
            handles.append(handle)
        done, errors = [], []
        start = threading.Barrier(len(themes))

        def attempt(handle, theme):
            start.wait()
            try:
                handle.write(theme)
                done.append(theme)
            except ThemeError as error:
                errors.append(error)

        threads = [
            threading.Thread(target=attempt, args=pair)
            for pair in zip(handles, themes, strict=True)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(done), 1, "a first save raced")
        self.assertEqual(len(errors), len(themes) - 1)
        self.assertEqual(load_theme(self.path).theme, done[0])

    def test_the_lock_is_a_stable_sibling_that_survives_replacement(self):
        handle = ThemeFile(self.path)
        handle.read_bytes()
        handle.write(DEFAULT_THEME)
        lock = self.directory / ".theme.toml.lock"
        first = lock.stat().st_ino
        handle.write(Theme.from_dict({"accent": {"foreground": ["red"]}}))
        self.assertEqual(lock.stat().st_ino, first)


class NonRegularInputTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))

    def test_a_fifo_config_is_refused_instead_of_blocking_startup(self):
        # Opening a FIFO for reading blocks until a writer appears, which would
        # hang the viewer before its first frame. This must return immediately.
        path = self.directory / "theme.toml"
        os.mkfifo(path)
        result = load_theme(path)
        self.assertEqual(result.theme, DEFAULT_THEME)
        self.assertIn("regular file", result.diagnostic)
        self.assertIsNotNone(ThemeFile(path).read_bytes()[1])

    def test_a_directory_config_is_refused_on_read_too(self):
        path = self.directory / "adirectory"
        path.mkdir()
        self.assertIsNotNone(load_theme(path).diagnostic)

    def test_a_symlink_to_a_regular_file_is_still_readable(self):
        real = self.directory / "real.toml"
        real.write_text('[accent]\nforeground = "red"\n')
        link = self.directory / "link.toml"
        link.symlink_to(real)
        self.assertEqual(load_theme(link).theme.roles["accent"].foreground, ("red",))


class OversizeTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.path = self.directory / "theme.toml"

    def test_saving_over_an_oversize_file_is_refused_rather_than_racing_blind(self):
        # Reads stop one byte past the cap, so an edit beyond it could not be
        # detected. Refuse the save instead of overwriting someone's work.
        self.path.write_text("# padding\n" * MAX_THEME_BYTES)
        handle = ThemeFile(self.path)
        self.assertEqual(len(handle.read_bytes()[0]), MAX_THEME_BYTES + 1)
        with self.assertRaises(ThemeError) as caught:
            handle.write(DEFAULT_THEME)
        self.assertIn("larger than", str(caught.exception))
        self.assertIn("unchanged", str(caught.exception))
        self.assertGreater(self.path.stat().st_size, MAX_THEME_BYTES)

    def test_the_digest_covers_length_so_a_truncating_edit_is_still_a_conflict(self):
        self.path.write_text('[accent]\nforeground = "red"\n')
        handle = ThemeFile(self.path)
        handle.read_bytes()
        self.path.write_text('[accent]\nforeground = "red"')
        with self.assertRaises(ThemeConflict):
            handle.write(DEFAULT_THEME)


class InstallTests(unittest.TestCase):
    def setUp(self):
        theme_module._INSTALLED.clear()
        self.addCleanup(theme_module._INSTALLED.clear)

    def test_install_sets_the_role_and_combination_pairs_and_precomputes_styles(self):
        curses = Curses()
        palette = DEFAULT_THEME.resolve(256)
        palette.install(curses)
        self.assertEqual(curses.started, 1)
        self.assertEqual(curses.defaults, 1)
        self.assertEqual(
            curses.pairs,
            {
                1: (16, 17),
                2: (18, 19),
                3: (20, 17),
                4: (21, 17),
                5: (22, 17),
                6: (16, 23),
                7: (24, 17),
                # Accent, muted and danger text on the selected row; accent
                # and muted text on the header bar.
                8: (20, 19),
                9: (21, 19),
                10: (24, 19),
                11: (20, 23),
                12: (21, 23),
            },
        )
        self.assertEqual([palette.style(role) for role in ROLES], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(palette.style("accent", "active"), 8)
        self.assertEqual(palette.style("muted", "header"), 12)

    def test_optional_roles_and_combinations_fall_back_when_pairs_run_out(self):
        curses = Curses(pair_limit=5)
        palette = DEFAULT_THEME.resolve(256)
        palette.install(curses)
        self.assertEqual(sorted(curses.pairs), [1, 2, 3, 4, 5])
        self.assertEqual(palette.style("header"), palette.style("normal"))
        self.assertEqual(palette.style("danger"), palette.style("accent") | Curses.A_BOLD)
        self.assertEqual(palette.style("accent", "active"), palette.style("accent"))
        self.assertEqual(palette.installed("header"), palette.installed("normal"))
        with self.assertRaises(ThemeError):
            Theme.from_dict({}).resolve(256).install(Curses(pair_limit=4))

    def test_attributes_are_folded_into_the_precomputed_style(self):
        theme = Theme.from_dict({"muted": {"attributes": ["bold", "underline"]}})
        palette = theme.resolve(256)
        palette.install(Curses())
        self.assertEqual(palette.style("muted"), 4 | Curses.A_BOLD | Curses.A_UNDERLINE)

    def test_style_before_install_is_a_clear_error_rather_than_a_wrong_color(self):
        with self.assertRaises(ThemeError):
            DEFAULT_THEME.resolve(256).style("active")

    def test_a_failed_install_rolls_back_to_the_previous_palette(self):
        curses = Curses()
        DEFAULT_THEME.resolve(256).install(curses)
        installed = dict(curses.pairs)
        broken = Theme.from_dict({"muted": {"foreground": "red"}}).resolve(256)
        curses._fail_on = 4
        with self.assertRaises(ThemeError) as caught:
            broken.install(curses)
        self.assertIn("previous colors were kept", str(caught.exception))
        self.assertEqual(roles(curses.pairs), roles(installed))

    def test_substituting_defaults_cannot_collapse_a_pair_into_one_color(self):
        # Root's repro: (0, -1) resolves legibly, but without default-color
        # support the background becomes 0 and the pair installs black on black.
        theme = Theme.from_dict({"normal": {"foreground": ["black"], "background": ["default"]}})
        palette = theme.resolve(8)
        self.assertEqual(palette.entries["normal"][:2], (0, -1))
        curses = Curses(default_colors=False)
        palette.install(curses)
        self.assertNotEqual(curses.pairs[1][0], curses.pairs[1][1])
        self.assertEqual(curses.pairs[1], (0, 7))
        self.assertEqual(palette.installed("normal"), (0, 7))

    def test_the_repair_moves_the_substituted_side_and_keeps_the_users_choice(self):
        for foreground, background, expected in (
            (["black"], ["default"], (0, 7)),
            (["default"], ["white"], (0, 7)),
            (["white"], ["default"], (7, 0)),
            (["default"], ["black"], (7, 0)),
        ):
            theme = Theme.from_dict(
                {"normal": {"foreground": foreground, "background": background}}
            )
            curses = Curses(default_colors=False)
            theme.resolve(8).install(curses)
            self.assertEqual(curses.pairs[1], expected, (foreground, background))

    def test_no_role_installs_an_invisible_pair_without_default_color_support(self):
        for role in ROLES:
            for foreground, background in (
                (["black"], ["default"]),
                (["default"], ["black"]),
                (["white"], ["default"]),
                (["default"], ["white"]),
                (["default"], ["default"]),
            ):
                theme = Theme.from_dict(
                    {role: {"foreground": foreground, "background": background}}
                )
                curses = Curses(default_colors=False)
                theme.resolve(8).install(curses)
                for number, pair in curses.pairs.items():
                    self.assertNotEqual(pair[0], pair[1], (role, foreground, background, number))
                    self.assertTrue(all(value >= 0 for value in pair), (role, number))

    def test_a_partial_failure_after_a_default_substitution_rolls_back_cleanly(self):
        # use_default_colors fails, an install succeeds with substituted colors,
        # then a later install fails partway. The restored pairs must be the
        # substituted numbers, never the unresolved -1.
        curses = Curses(default_colors=False)
        first = Theme.from_dict({"normal": {"foreground": ["black"], "background": ["default"]}})
        first.resolve(8).install(curses)
        installed = dict(curses.pairs)
        self.assertEqual(installed[1], (0, 7))
        curses.pairs.clear()
        curses._fail_on = 3
        with self.assertRaises(ThemeError) as caught:
            Theme.from_dict({"accent": {"foreground": ["red"]}}).resolve(8).install(curses)
        self.assertIn("previous colors were kept", str(caught.exception))
        self.assertEqual(curses.pairs, roles(installed))
        self.assertTrue(all(min(pair) >= 0 for pair in curses.pairs.values()))

    def test_installed_reports_what_was_given_to_init_pair(self):
        palette = preset_theme("forest").resolve(256)
        with self.assertRaises(ThemeError):
            palette.installed("normal")
        palette.install(Curses())
        self.assertEqual(palette.installed("normal"), (-1, -1))
        self.assertEqual(palette.installed("active"), (231, 238))

    def test_rollback_replays_the_numbers_actually_installed_not_the_logical_ones(self):
        # Without default-color support the logical -1 is substituted before
        # init_pair, so a rollback replaying -1 would fail and strand the
        # half-applied palette.
        curses = Curses(default_colors=False)
        preset_theme("forest").resolve(256).install(curses)
        installed = dict(curses.pairs)
        self.assertEqual(installed[1], (7, 0))
        curses.pairs.clear()
        curses._fail_on = 4
        with self.assertRaises(ThemeError):
            Theme.from_dict({"muted": {"foreground": "red"}}).resolve(256).install(curses)
        self.assertEqual(curses.pairs, roles(installed))
        self.assertTrue(all(min(pair) >= 0 for pair in curses.pairs.values()))

    def test_terminals_without_default_color_support_get_concrete_colors(self):
        curses = Curses(default_colors=False)
        preset_theme("forest").resolve(256).install(curses)
        self.assertEqual(curses.pairs[1], (7, 0))
        self.assertEqual(curses.pairs[3], (108, 0))

    def test_installing_costs_one_pair_per_role_and_combination_so_an_apply_stays_cheap(self):
        curses = Curses()
        palette = DEFAULT_THEME.resolve(256)
        palette.install(curses)
        self.assertEqual(len(curses.pairs), len(ROLES) + len(COMBINATIONS))
        self.assertLessEqual(max(curses.pairs), 12)


class StateTransportTests(unittest.TestCase):
    """The launcher validates once and transports the result to the viewer."""

    def test_a_snapshot_round_trips_without_touching_the_file(self):
        theme = Theme.from_dict({"accent": {"foreground": ["red"]}})
        self.assertEqual(parse_theme_state(theme.to_toml()), theme)
        self.assertEqual(parse_theme_state(DEFAULT_THEME.to_toml()), DEFAULT_THEME)

    def test_an_invalid_or_oversize_snapshot_is_refused(self):
        with self.assertRaises(ValueError):
            parse_theme_state("[nope]\n")
        with self.assertRaises(ValueError):
            parse_theme_state("# padding\n" * MAX_THEME_BYTES)


class AttributeSurfaceTests(unittest.TestCase):
    def test_every_named_attribute_exists_in_curses(self):
        import importlib

        curses = importlib.import_module("curses")
        for name in ATTRIBUTES:
            self.assertTrue(hasattr(curses, f"A_{name.upper()}"), name)


if __name__ == "__main__":
    unittest.main()
