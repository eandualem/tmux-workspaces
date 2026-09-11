import curses
import unittest
from dataclasses import dataclass, replace
from types import MappingProxyType

from tmux_workspaces.theme_editor import FIELDS, MAX_VALUE, ThemeEditor, fit_labels

ROLES = ("normal", "active", "muted", "accent")
LABELS = {"normal": "Normal", "active": "Selected", "muted": "Muted", "accent": "Accent"}
NAMES = {"default", "white", "blue", "cyan", "231", "238", "245", "108", "7"}
ATTRIBUTES = {"bold", "dim", "reverse", "standout", "underline"}


@dataclass(frozen=True)
class FakeRole:
    foreground: tuple[str, ...]
    background: tuple[str, ...] = ("default",)
    attributes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeTheme:
    """Stand-in shaped like tmux_workspaces.theme.Theme."""

    roles: MappingProxyType

    def with_role(self, role, **changes):
        for field, values in changes.items():
            if field not in FIELDS:
                raise ValueError(f"{role}: unknown field {field!r}")
            allowed = ATTRIBUTES if field == "attributes" else NAMES
            if field != "attributes" and not values:
                raise ValueError(f"{role}.{field}: needs at least one color")
            for value in values:
                if value not in allowed:
                    raise ValueError(f"{role}.{field}: {value!r} is not supported here")
        updated = replace(self.roles[role], **{f: tuple(v) for f, v in changes.items()})
        return FakeTheme(MappingProxyType(dict(self.roles) | {role: updated}))


def theme(**overrides):
    roles = {role: FakeRole(("default",)) for role in ROLES}
    roles["active"] = FakeRole(("231", "white"), ("238", "blue"))
    roles["accent"] = FakeRole(("108", "cyan"))
    roles["muted"] = FakeRole(("245", "white"))
    return FakeTheme(MappingProxyType(roles | overrides))


DEFAULTS = theme()


class ThemeEditorTests(unittest.TestCase):
    def setUp(self):
        self.theme = theme(accent=FakeRole(("blue",), ("default",), ("bold",)))
        self.saved, self.installed = [], []
        self.editor = self.build()

    def build(self, save=None, **kwargs):
        return ThemeEditor(
            self.theme,
            save or self.saved.append,
            self.installed.append,
            DEFAULTS,
            labels=LABELS,
            **kwargs,
        )

    def type(self, text):
        for char in text:
            self.editor.key(char)

    def goto(self, role, field):
        self.editor.index = self.editor.targets.index((role, field))

    def test_overlong_field_stays_open_without_truncation_or_save(self):
        value = "default, " * 7 + "238"
        self.assertGreater(len(value), MAX_VALUE)
        self.editor.edit()
        self.type(value)
        self.editor.apply()
        self.assertEqual(self.editor.field.value, value)
        self.assertIn("at most 60 characters", self.editor.message)
        self.assertFalse(self.editor.closed)
        self.assertEqual(self.saved, [])
        self.assertEqual(self.installed, [])

    def test_keypad_enter_opens_and_commits_the_color_field(self):
        self.editor.key(curses.KEY_ENTER)
        self.assertIsNotNone(self.editor.field)
        self.type("blue")
        self.editor.key(curses.KEY_ENTER)
        self.assertIsNone(self.editor.field)
        self.assertEqual(self.editor.draft.roles["normal"].foreground, ("blue",))
        self.assertEqual(self.installed, [self.editor.draft])
        self.assertEqual(self.saved, [])

    def test_rows_cover_every_role_and_field_with_one_selection(self):
        rows = self.editor.rows()
        # One row per role field, then the preset row that summarises them.
        self.assertEqual(len(rows), len(ROLES) * len(FIELDS) + 1)
        self.assertEqual(rows[-1][:3], ("Preset", "preset", "custom"))
        self.assertEqual([role for role, *_ in rows[:3]], ["Normal"] * 3)
        self.assertEqual([field for _, field, *_ in rows[:3]], list(FIELDS))
        self.assertEqual(
            rows[rows.index(("Selected", "foreground", "231, white", False))][2], "231, white"
        )
        self.assertEqual([row[3] for row in rows].count(True), 1)
        self.assertTrue(rows[0][3])

    def test_unset_attributes_read_as_none_rather_than_blank(self):
        self.assertEqual(self.editor.rows()[2][2], "none")

    def test_field_names_stay_distinct_and_fit_a_narrow_sidebar(self):
        rows = self.editor.rows()
        for width in range(8, 24):
            labels = fit_labels(rows, width)
            self.assertEqual(len(set(labels)), len(rows), width)
            self.assertTrue(all(len(label) <= width for label in labels), width)
        self.assertEqual(fit_labels(rows, 20)[:2], ["Normal text", "Normal background"])
        self.assertEqual(fit_labels(rows, 14)[:2], ["Normal fg", "Normal bg"])
        self.assertEqual(fit_labels(rows, 8)[3:5], ["Sele… fg", "Sele… bg"])

    def test_a_refused_restore_keeps_the_editor_open_with_its_reason(self):
        refusals = []

        def install(theme):
            if refusals:
                raise ValueError("cannot install theme colors: the previous colors were kept")
            self.installed.append(theme)

        editor = ThemeEditor(self.theme, self.saved.append, install, DEFAULTS, labels=LABELS)
        editor.key("\n")
        for char in "blue":
            editor.key(char)
        editor.key("\n")
        refusals.append(True)
        editor.key("\x1b")
        self.assertFalse(editor.closed)
        self.assertIn("previous colors were kept", editor.message)
        refusals.clear()
        editor.key("\x1b")
        self.assertTrue(editor.closed)
        self.assertEqual(editor.draft, self.theme)
        self.assertEqual(self.installed[-1], self.theme)

    def test_colors_the_terminal_refuses_are_never_written(self):
        def refuse(theme):
            raise ValueError("cannot install theme colors: the previous colors were kept")

        editor = ThemeEditor(self.theme, self.saved.append, refuse, DEFAULTS, labels=LABELS)
        editor.key("\n")
        for char in "blue":
            editor.key(char)
        editor.key("\n")
        self.assertFalse(editor.installed)
        self.assertIn("previous colors were kept", editor.message)
        editor.key("a")
        self.assertEqual(self.saved, [])
        self.assertFalse(editor.closed)
        self.assertEqual(editor.draft.roles["normal"].foreground, ("blue",))

    def test_selection_moves_and_wraps_in_both_directions(self):
        self.editor.key(curses.KEY_DOWN)
        self.assertEqual(self.editor.target, ("normal", "background"))
        self.editor.key(curses.KEY_UP)
        self.editor.key(curses.KEY_UP)
        self.assertEqual(self.editor.target, ("preset", "preset"))
        self.editor.key(curses.KEY_UP)
        self.assertEqual(self.editor.target, ("accent", "attributes"))

    def test_accepted_value_previews_immediately_without_saving(self):
        self.goto("active", "background")
        self.editor.key("\n")
        self.type("blue")
        self.editor.key("\n")
        self.assertEqual(self.editor.draft.roles["active"].background, ("blue",))
        self.assertEqual(self.installed[-1], self.editor.draft)
        self.assertEqual(self.saved, [])
        self.assertIsNone(self.editor.field)
        self.assertTrue(self.editor.changed)
        self.assertFalse(self.editor.closed)

    def test_ordered_fallbacks_are_edited_as_a_comma_separated_list(self):
        self.goto("active", "foreground")
        self.editor.key("\n")
        self.type("231, white")
        self.editor.key("\n")
        self.assertEqual(self.editor.draft.roles["active"].foreground, ("231", "white"))

    def test_attributes_clear_to_an_empty_list_and_keep_contrast_cues(self):
        self.goto("accent", "attributes")
        self.editor.key("\n")
        self.editor.field.value = ""
        self.editor.key("\n")
        self.assertEqual(self.editor.draft.roles["accent"].attributes, ())
        self.goto("active", "attributes")
        self.editor.key("\n")
        self.type("reverse")
        self.editor.key("\n")
        self.assertEqual(self.editor.draft.roles["active"].attributes, ("reverse",))

    def test_rejected_value_keeps_the_working_theme_and_the_field_open(self):
        self.editor.key("\n")
        self.type("mauve")
        self.editor.key("\n")
        self.assertIn("mauve", self.editor.message)
        self.assertIsNotNone(self.editor.field)
        self.assertEqual(self.editor.draft, self.theme)
        self.assertEqual(self.installed, [])
        self.assertEqual(self.saved, [])

    def test_empty_color_is_refused_instead_of_erasing_a_role(self):
        self.editor.key("\n")
        self.editor.field.value = ""
        self.editor.key("\n")
        self.assertIn("at least one color", self.editor.message)
        self.assertEqual(self.editor.draft, self.theme)

    def test_rejected_value_holds_the_selection_on_the_field_it_belongs_to(self):
        self.editor.key("\n")
        self.type("mauve")
        self.editor.key(curses.KEY_DOWN)
        self.assertEqual(self.editor.target, ("normal", "foreground"))
        self.editor.field.value = "blue"
        self.editor.key("\n")
        self.assertIsNone(self.editor.field)
        self.editor.key(curses.KEY_DOWN)
        self.assertEqual(self.editor.target, ("normal", "background"))

    def test_escape_in_a_field_leaves_the_whole_editor_in_one_press(self):
        self.editor.key("\n")
        self.type("7")
        self.editor.key("\x1b")
        self.assertIsNone(self.editor.field)
        self.assertTrue(self.editor.closed)
        self.assertFalse(self.editor.saved)
        self.assertEqual(self.editor.draft, self.theme)
        self.assertEqual(self.saved, [])

    def test_typed_characters_reach_an_open_field_instead_of_the_commands(self):
        self.editor.key("\n")
        self.type("ad")
        self.assertEqual(self.editor.field.value, "ad")
        self.assertEqual(self.saved, [])
        self.assertEqual(self.editor.draft, self.theme)

    def test_cancel_restores_and_reinstalls_the_theme_the_editor_opened_with(self):
        self.editor.key("\n")
        self.type("7")
        self.editor.key("\n")
        self.editor.key("\x1b")
        self.assertTrue(self.editor.closed)
        self.assertFalse(self.editor.saved)
        self.assertEqual(self.editor.draft, self.theme)
        self.assertEqual(self.installed[-1], self.theme)
        self.assertEqual(self.saved, [])

    def test_defaults_are_previewed_and_only_persist_when_applied(self):
        self.editor.key("d")
        self.assertEqual(self.editor.draft, DEFAULTS)
        self.assertEqual(self.installed[-1], DEFAULTS)
        self.assertEqual(self.saved, [])
        self.editor.key("a")
        self.assertEqual(self.saved, [DEFAULTS])
        self.assertTrue(self.editor.saved)
        self.assertTrue(self.editor.closed)

    def test_apply_commits_an_open_field_before_saving_once(self):
        self.editor.key("\n")
        self.type("245")
        self.editor.apply()
        self.assertEqual(self.editor.draft.roles["normal"].foreground, ("245",))
        self.assertEqual(self.saved, [self.editor.draft])
        self.assertTrue(self.editor.saved)

    def test_apply_with_an_invalid_field_saves_nothing_and_stays_open(self):
        self.editor.key("\n")
        self.type("mauve")
        self.editor.apply()
        self.assertEqual(self.saved, [])
        self.assertFalse(self.editor.closed)
        self.assertIn("mauve", self.editor.message)

    def test_conflicting_save_keeps_the_draft_and_the_working_theme(self):
        def refuse(theme):
            raise ValueError("Colors file changed in another window")

        editor = self.build(refuse)
        editor.key("\n")
        for char in "blue":
            editor.key(char)
        editor.key("\n")
        editor.key("a")
        self.assertEqual(editor.message, "Error: Colors file changed in another window")
        self.assertFalse(editor.closed)
        self.assertFalse(editor.saved)
        self.assertEqual(editor.draft.roles["normal"].foreground, ("blue",))
        self.assertEqual(editor.working, self.theme)
        editor.key("\x1b")
        self.assertEqual(editor.draft, self.theme)
        self.assertEqual(self.installed[-1], self.theme)

    def test_read_only_file_is_announced_and_never_discards_colors(self):
        def refuse(theme):
            raise OSError(13, "Permission denied")

        editor = self.build(refuse, writable=False)
        self.assertIn("read-only", editor.message)
        editor.key("d")
        editor.key("a")
        self.assertIn("Permission denied", editor.message)
        self.assertFalse(editor.closed)
        self.assertEqual(editor.draft, DEFAULTS)

    def test_mouse_route_edits_the_clicked_field_directly(self):
        index = self.editor.targets.index(("muted", "foreground"))
        self.editor.edit(index)
        self.assertEqual(self.editor.target, ("muted", "foreground"))
        self.assertEqual(self.editor.field.value, "245, white")
        self.assertTrue(self.editor.field.selected)

    def test_mouse_route_cannot_leave_an_invalid_field_behind(self):
        self.editor.key("\n")
        self.type("mauve")
        self.editor.edit(self.editor.targets.index(("accent", "attributes")))
        self.assertEqual(self.editor.target, ("normal", "foreground"))
        self.assertEqual(self.editor.field.value, "mauve")

    def test_representation_tracks_draft_state_for_frame_caching(self):
        before = repr(self.editor)
        self.assertEqual(before, repr(self.editor))
        self.editor.key(curses.KEY_DOWN)
        self.assertNotEqual(before, repr(self.editor))
        moved = repr(self.editor)
        self.editor.key("\n")
        self.type("blue")
        self.editor.key("\n")
        self.assertNotEqual(moved, repr(self.editor))


if __name__ == "__main__":
    unittest.main()
