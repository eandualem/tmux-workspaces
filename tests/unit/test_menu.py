import unittest

from tmux_workspaces.menu import Entry, Selection


def entries(*keys):
    return [Entry(key, key.split(":", 1)[-1], lambda key=key: key) for key in keys]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.selection = Selection()

    def active(self):
        return self.selection.entries[self.selection.index].key

    def test_first_entry_is_active_and_activation_runs_its_own_callback(self):
        visible = self.selection.show(entries("session:alpha", "session:beta"), 5)
        self.assertEqual([entry.label for entry in visible], ["alpha", "beta"])
        self.assertEqual(self.selection.entry().action(), "session:alpha")
        self.selection.move(1)
        self.assertEqual(self.selection.entry().action(), "session:beta")

    def test_rows_expose_labels_and_callbacks_for_the_drawing_contract(self):
        self.selection.show(entries("session:alpha"), 5)
        ((label, action),) = self.selection.rows
        self.assertEqual((label, action()), ("alpha", "session:alpha"))

    def test_empty_options_activate_nothing_and_survive_navigation(self):
        self.assertEqual(self.selection.show([], 5), [])
        self.selection.move(1)
        self.selection.move(-1)
        self.assertIsNone(self.selection.entry())
        self.assertEqual(self.selection.index, 0)

    def test_movement_stops_at_both_ends_without_wrapping(self):
        self.selection.show(entries("row:one", "row:two", "row:three"), 5)
        self.selection.move(-1)
        self.assertEqual(self.active(), "row:one")
        self.selection.move(9)
        self.assertEqual(self.active(), "row:three")

    def test_sorted_insertion_keeps_the_active_entry_instead_of_its_position(self):
        self.selection.show(entries("session:builder", "session:manager"), 5)
        self.selection.move(1)
        self.assertEqual(self.active(), "session:manager")
        # A newly discovered session sorts before the active row.
        self.selection.show(entries("session:archivist", "session:builder", "session:manager"), 5)
        self.assertEqual((self.active(), self.selection.index), ("session:manager", 2))
        self.assertEqual(self.selection.entry().action(), "session:manager")

    def test_vanished_entry_refuses_to_activate_its_neighbour(self):
        self.selection.show(entries("session:builder", "session:manager"), 5)
        self.selection.move(1)
        self.selection.show(entries("session:builder", "session:reviewer"), 5)
        self.assertTrue(self.selection.stale)
        self.assertIsNone(self.selection.entry())
        # Choosing again clears the refusal.
        self.selection.move(0)
        self.assertFalse(self.selection.stale)
        self.assertEqual(self.selection.entry().action(), "session:reviewer")

    def test_row_arriving_after_an_empty_frame_is_not_activated_unseen(self):
        self.selection.show(entries("session:builder"), 5)
        self.selection.show([], 5)
        self.assertIsNone(self.selection.entry())
        # A session arrives while the drawn frame still shows no results.
        self.selection.show(entries("session:newcomer"), 5, drawn=False)
        self.assertIsNone(self.selection.entry())
        # Once the row has actually been drawn it is the user's choice again.
        self.selection.show(entries("session:newcomer"), 5)
        self.assertEqual(self.selection.entry().action(), "session:newcomer")

    def test_a_frame_that_showed_no_rows_disarms_the_chosen_entry(self):
        self.selection.show(entries("session:builder", "session:manager"), 5)
        self.selection.move(1)
        self.selection.hide()
        self.assertIsNone(self.selection.entry())
        self.assertEqual(self.active(), "session:manager")
        # Drawing the rows again re-arms the same entry, not a different one.
        self.selection.show(entries("session:builder", "session:manager"), 5)
        self.assertEqual(self.selection.entry().action(), "session:manager")

    def test_activation_rebuild_never_adopts_a_replacement_row(self):
        self.selection.show(entries("session:builder", "session:manager"), 5)
        self.selection.move(1)
        self.selection.show(entries("session:builder", "session:manager"), 5, drawn=False)
        self.assertEqual(self.selection.entry().action(), "session:manager")
        self.selection.show(entries("session:builder", "session:newcomer"), 5, drawn=False)
        self.assertIsNone(self.selection.entry())

    def test_repeated_labels_stay_apart_because_keys_identify_the_row(self):
        rows = [
            Entry("workspace:a", "Shared", lambda: "a"),
            Entry("workspace:b", "Shared", lambda: "b"),
        ]
        self.selection.show(rows, 5)
        self.selection.move(1)
        self.assertEqual(self.selection.entry().action(), "b")
        self.selection.show([rows[1], rows[0]], 5)
        self.assertEqual(self.selection.entry().action(), "b")
        self.assertEqual(self.selection.index, 0)

    def test_filter_edit_returns_to_the_first_result_and_top_of_the_list(self):
        rows = entries(*[f"row:{n}" for n in range(20)])
        self.selection.show(rows, 4)
        self.selection.move(9)
        self.selection.show(rows, 4)
        self.assertGreater(self.selection.offset, 0)
        self.selection.first()
        self.selection.show(rows, 4)
        self.assertEqual((self.selection.index, self.selection.offset), (0, 0))

    def test_moving_beyond_the_window_scrolls_the_active_row_into_view(self):
        rows = entries(*[f"row:{n}" for n in range(10)])
        self.selection.show(rows, 3)
        for _ in range(4):
            self.selection.move(1)
            visible = self.selection.show(rows, 3)
            self.assertIn(self.active(), [entry.key for entry in visible])
        self.assertEqual(self.selection.offset, 2)
        for _ in range(4):
            self.selection.move(-1)
            visible = self.selection.show(rows, 3)
            self.assertIn(self.active(), [entry.key for entry in visible])
        self.assertEqual(self.selection.offset, 0)

    def test_scrolling_the_window_pulls_the_active_row_inside_it(self):
        rows = entries(*[f"row:{n}" for n in range(10)])
        self.selection.show(rows, 3)
        self.selection.scroll(4)
        visible = self.selection.show(rows, 3)
        self.assertEqual([entry.key for entry in visible], ["row:4", "row:5", "row:6"])
        self.assertEqual(self.active(), "row:4")
        self.selection.scroll(-2)
        self.selection.show(rows, 3)
        self.assertEqual(self.active(), "row:4")
        self.selection.scroll(-2)
        self.selection.show(rows, 3)
        self.assertEqual(self.active(), "row:2")

    def test_scrolling_past_the_end_clamps_to_the_last_window(self):
        rows = entries(*[f"row:{n}" for n in range(6)])
        self.selection.show(rows, 4)
        self.selection.scroll(40)
        visible = self.selection.show(rows, 4)
        self.assertEqual([entry.key for entry in visible], ["row:2", "row:3", "row:4", "row:5"])
        self.assertEqual(self.selection.offset, 2)

    def test_reset_clears_rows_scroll_and_remembered_entry(self):
        self.selection.show(entries("row:one", "row:two"), 1)
        self.selection.move(1)
        self.selection.reset()
        self.assertEqual((self.selection.entries, self.selection.index), ([], 0))
        self.assertEqual((self.selection.offset, self.selection.active), (0, None))
        self.selection.show(entries("row:two", "row:one"), 5)
        self.assertEqual(self.active(), "row:two")


if __name__ == "__main__":
    unittest.main()
