"""The empty pane's chooser: selection, the action it sends, and its loop."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tmux_workspaces import chooser as module
from tmux_workspaces.chooser import Chooser, draw, run
from tmux_workspaces.controls import pane_choice

TAB, LEAF = "a" * 12, "b" * 12


class StopFixture(Exception):
    pass


class FakeCurses(SimpleNamespace):
    class error(Exception):
        pass


def fake_curses(keys):
    keys = list(keys)

    def getch():
        if not keys:
            raise StopFixture
        return keys.pop(0)

    screen = Mock()
    screen.getmaxyx.return_value = (20, 60)
    screen.getch.side_effect = getch
    curses = FakeCurses(
        A_BOLD=1,
        A_DIM=2,
        A_REVERSE=4,
        KEY_UP=259,
        KEY_DOWN=258,
        KEY_ENTER=343,
        KEY_MOUSE=409,
        ALL_MOUSE_EVENTS=1,
        BUTTON1_PRESSED=2,
        BUTTON1_CLICKED=4,
        curs_set=Mock(),
        mousemask=Mock(),
        getmouse=Mock(return_value=(0, 3, 0, 0, 2)),
    )
    return screen, curses


class ChooserStateTests(unittest.TestCase):
    def test_terminal_is_first_and_sessions_follow_sorted(self):
        chooser = Chooser(TAB, LEAF)
        chooser.update({"zeta": {"online": True, "state": "idle"}, "alpha": {"online": False}}, "")
        self.assertEqual(
            chooser.rows(), [(module.TERMINAL, ""), ("alpha", "offline"), ("zeta", "idle")]
        )
        self.assertEqual(chooser.action(), f"choose-terminal:{TAB}:{LEAF}")
        chooser.move(1)
        self.assertEqual(chooser.action(), f"choose-session:{TAB}:{LEAF}:alpha")
        chooser.move(-2)
        self.assertEqual(chooser.selected_name(), "zeta")

    def test_a_roster_change_keeps_the_selected_session_or_clamps(self):
        chooser = Chooser(TAB, LEAF)
        chooser.update({"a": {"online": True}, "b": {"online": True}}, "")
        chooser.select(2)
        chooser.update({"b": {"online": True}, "c": {"online": True}}, "")
        self.assertEqual(chooser.selected_name(), "b")
        chooser.update({}, "Session discovery unavailable; states stale")
        self.assertEqual(chooser.index, 0)
        self.assertIn("unavailable", chooser.error)

    def test_every_action_it_can_send_is_a_valid_pane_choice(self):
        chooser = Chooser(TAB, LEAF)
        chooser.update({"-Ops [α] 'x'; $value": {"online": True, "state": "busy"}}, "")
        for index in range(chooser.count):
            chooser.select(index)
            self.assertIsNotNone(pane_choice(chooser.action()), chooser.action())
        chooser.select(1)
        self.assertEqual(pane_choice(chooser.action())[3], "-Ops [α] 'x'; $value")


class ChooserDrawTests(unittest.TestCase):
    def test_draw_maps_rows_to_choices_and_marks_the_selection(self):
        screen, curses = fake_curses([])
        chooser = Chooser(TAB, LEAF)
        chooser.update({"work": {"online": True, "state": "idle"}}, "")
        chooser.select(1)
        hits = draw(screen, chooser, curses)
        texts = [(call.args[0], call.args[2]) for call in screen.addnstr.call_args_list]
        self.assertIn((1, module.TITLE), texts)
        self.assertIn((4, "  " + module.TERMINAL), texts)
        self.assertIn((5, module.ROSTER_HEADING), texts)
        # The selected row is padded to the width so it reads as one bar.
        self.assertIn((6, "▸ work"), [(row, text.rstrip()) for row, text in texts])
        self.assertEqual(hits, {4: 0, 6: 1})
        self.assertIn(module.HINT, [call.args[2] for call in screen.addnstr.call_args_list])

    def test_a_roster_taller_than_the_pane_scrolls_with_the_selection(self):
        """Keyboard and mouse agree: only drawn rows are clickable, and the
        selection is always among them."""
        screen, curses = fake_curses([])
        chooser = Chooser(TAB, LEAF)
        chooser.update({f"s{index:02d}": {"online": True} for index in range(30)}, "")
        hits = draw(screen, chooser, curses)
        # Twenty rows: fourteen list rows (4..17), terminal, heading, twelve sessions.
        self.assertEqual(hits, {4: 0, **{row: row - 5 for row in range(6, 18)}})
        texts = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertIn(module.MORE_BELOW, texts)
        self.assertNotIn(module.MORE_ABOVE, texts)
        chooser.select(25)
        hits = draw(screen, chooser, curses)
        self.assertIn(25, hits.values())
        self.assertNotIn(0, hits.values(), "the terminal row scrolled off with the selection")
        texts = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertIn(module.MORE_ABOVE, texts)
        # Moving back up scrolls only as far as needed; the last row stays clickable.
        chooser.move(-1)
        hits = draw(screen, chooser, curses)
        self.assertEqual(min(hits.values()), 12)
        chooser.select(30)
        hits = draw(screen, chooser, curses)
        self.assertEqual(max(hits.values()), 30)
        chooser.select(0)
        hits = draw(screen, chooser, curses)
        self.assertEqual(hits[4], 0)
        # A tiny pane still keeps the selection on screen.
        screen.getmaxyx.return_value = (7, 40)
        chooser.select(3)
        self.assertEqual(draw(screen, chooser, curses), {4: 3})

    def test_an_empty_roster_says_so_and_a_narrow_pane_never_raises(self):
        screen, curses = fake_curses([])
        screen.getmaxyx.return_value = (3, 8)
        screen.addnstr.side_effect = curses.error
        draw(screen, Chooser(TAB, LEAF), curses)
        screen.getmaxyx.return_value = (20, 60)
        screen.addnstr.side_effect = None
        draw(screen, Chooser(TAB, LEAF), curses)
        self.assertIn(module.EMPTY_ROSTER, [call.args[2] for call in screen.addnstr.call_args_list])


class ChooserRunTests(unittest.TestCase):
    def source(self, sessions):
        return Mock(snapshot=Mock(return_value=(sessions, "")))

    def test_enter_sends_the_selection_and_waits_for_the_viewer(self):
        screen, curses = fake_curses([258, 10])
        chooser = Chooser(TAB, LEAF)
        with (
            patch.object(module, "send_action") as send,
            self.assertRaises(StopFixture),
        ):
            run(
                screen,
                chooser,
                self.source({"work": {"online": True}}),
                "/tmp/a.sock",
                curses,
                write=Mock(),
            )
        send.assert_called_once_with("/tmp/a.sock", f"choose-session:{TAB}:{LEAF}:work", wait=True)
        self.assertEqual(chooser.message, "Opening…")

    def test_a_click_on_a_session_row_chooses_it(self):
        """Both mouse routes: a curses event, and a raw SGR press the pane asked for."""
        raw = [27, *(ord(c) for c in "[<0;5;7M")]
        for name, keys, mouse in (("curses", [409], (0, 3, 6, 0, 2)), ("sgr", raw, None)):
            with self.subTest(route=name):
                screen, curses = fake_curses(keys)
                if mouse:
                    curses.getmouse.return_value = mouse
                chooser = Chooser(TAB, LEAF)
                write = Mock()
                with (
                    patch.object(module, "send_action") as send,
                    self.assertRaises(StopFixture),
                ):
                    run(
                        screen,
                        chooser,
                        self.source({"work": {"online": True}}),
                        "/tmp/a.sock",
                        curses,
                        write=write,
                    )
                send.assert_called_once_with(
                    "/tmp/a.sock", f"choose-session:{TAB}:{LEAF}:work", wait=True
                )
                self.assertEqual(
                    [call.args[0] for call in write.call_args_list],
                    [module.MOUSE_ON, module.MOUSE_OFF],
                )

    def test_a_release_or_another_button_never_chooses(self):
        for sequence in ("[<0;5;7m", "[<2;5;7M", "[<64;5;7M", "[A"):
            with self.subTest(sequence=sequence):
                self.assertIsNone(module.clicked_row(sequence))
        self.assertEqual(module.clicked_row("[<0;5;7M"), 6)

    def test_a_silent_viewer_is_reported_and_the_choice_stays_offered(self):
        screen, curses = fake_curses([10])
        chooser = Chooser(TAB, LEAF)
        with (
            patch.object(module, "send_action", side_effect=OSError("gone")) as send,
            self.assertRaises(StopFixture),
        ):
            run(screen, chooser, self.source({}), "/tmp/a.sock", curses, write=Mock())
        self.assertEqual(send.call_count, 1)
        self.assertEqual(chooser.message, "The viewer did not respond; try again")
        # The choice is still offered: a second Enter sends it again.
        screen, curses = fake_curses([10, 10])
        with (
            patch.object(module, "send_action", side_effect=[OSError("gone"), None]) as send,
            self.assertRaises(StopFixture),
        ):
            run(screen, chooser, self.source({}), "/tmp/a.sock", curses, write=Mock())
        self.assertEqual(send.call_count, 2)
        self.assertEqual(chooser.message, "Opening…")

    def test_after_a_choice_further_keys_are_ignored_until_the_notice_expires(self):
        screen, curses = fake_curses([10, 10, 10])
        chooser = Chooser(TAB, LEAF)
        with (
            patch.object(module, "send_action") as send,
            patch.object(module.time, "monotonic", side_effect=[1.0, 1.0, 1.0, 2.0, 2.0, 2.0]),
            self.assertRaises(StopFixture),
        ):
            run(screen, chooser, self.source({}), "/tmp/a.sock", curses, write=Mock())
        self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
