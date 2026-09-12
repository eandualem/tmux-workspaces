"""Palette probes accept tmux reply versions without treating timeouts as reset."""

import unittest

from tests.integration.smoke_attachment_palette import painted_color, palette_reply


class PaletteReplyTests(unittest.TestCase):
    def test_rendered_rgb_reset_and_indexed_colors(self):
        output = b"\x1b[38;2;204;204;204mBEFORE\x1b[0mPLAIN\x1b[38;5;16mAFTER"
        self.assertEqual(painted_color(output, "BEFORE"), (204, 204, 204))
        self.assertIsNone(painted_color(output, "PLAIN"))
        self.assertEqual(painted_color(output, "AFTER"), ("index", 16))

    def test_background_components_do_not_change_foreground(self):
        output = b"\x1b[38;2;204;204;204m\x1b[48;2;38;5;16mMARKER"
        self.assertEqual(painted_color(output, "MARKER"), (204, 204, 204))

    def test_mixed_color_marker_does_not_claim_one_uniform_color(self):
        output = b"\x1b[38;5;16mMAR\x1b[38;2;204;204;204mKER"
        self.assertIsNone(painted_color(output, "MARKER"))

    def test_indexed_and_indexless_replies_report_the_same_color(self):
        for index in (b"16;", b""):
            response = b"\x1b]4;" + index + b"rgb:CCCC/CCCC/CCCC\x1b\\\x1b[0n"
            self.assertEqual(palette_reply(response, 16), "rgb:cccc/cccc/cccc")

    def test_only_an_ordered_status_reply_confirms_no_override(self):
        self.assertIsNone(palette_reply(b"\x1b[0n", 16))
        with self.assertRaisesRegex(ValueError, "no completion"):
            palette_reply(b"", 16)

    def test_wrong_slot_extra_color_and_unfinished_response_are_rejected(self):
        for response in (
            b"\x1b]4;7;rgb:1212/3434/5656\x1b\\\x1b[0n",
            b"\x1b]4;rgb:cccc/cccc/cccc\x1b\\\x1b]4;rgb:1212/3434/5656\x1b\\\x1b[0n",
            b"\x1b]4;16;rgb:cccc/cccc/cccc\x1b\\",
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                palette_reply(response, 16)
