import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock

from tmux_workspaces.popup import Frame


class FooterTests(unittest.TestCase):
    def test_a_long_path_keeps_its_file_name_and_the_help_key(self):
        screen = Mock()
        screen.getmaxyx.return_value = (10, 60)
        frame = Frame(screen, SimpleNamespace(error=Exception), defaultdict(int), None, None)
        path = "/var/folders/_j/t5mp6b0d16lbcqjgkbtmp3sm0000gn/T/library/theme.toml"
        frame.footer([("Save F2", "pill"), ("Cancel F10", "muted/header")], path, "Help F1")
        drawn = {call.args[2]: call.args[1] for call in screen.addstr.call_args_list}
        shown = next(text for text in drawn if text.startswith("…"))
        self.assertTrue(shown.endswith("/theme.toml"))
        self.assertEqual(drawn["Help F1"], 60 - 2 - len("Help F1"))
        self.assertLess(drawn[shown] + len(shown), drawn["Help F1"])


if __name__ == "__main__":
    unittest.main()
