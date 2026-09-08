import contextlib
import curses
import os
import pty
import select
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces.events import RESIZE_CHECK_SECONDS, InputEvents


class InputEventTests(unittest.TestCase):
    def setUp(self):
        self.reader, self.writer = os.pipe()
        self.receiver, self.sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.screen = Mock()
        self.screen.encoding = "utf-8"
        self.screen.getch.return_value = -1
        self.events = InputEvents(self.screen, self.receiver, self.reader)
        self.addCleanup(os.close, self.reader)
        self.addCleanup(os.close, self.writer)
        self.addCleanup(self.receiver.close)
        self.addCleanup(self.sender.close)

    def test_curses_buffer_is_drained_without_waiting_on_empty_stdin(self):
        self.screen.getch.side_effect = [0xC3, 0xA9, curses.KEY_RESIZE, ord("x")]
        with patch("tmux_workspaces.events.select.select") as waiting:
            self.assertEqual(self.events.read_or_wait(time.monotonic() + 10), "é")
            self.assertEqual(self.events.read_or_wait(time.monotonic() + 10), curses.KEY_RESIZE)
            self.assertEqual(self.events.read_or_wait(time.monotonic() + 10), "x")
        waiting.assert_not_called()

    def test_partial_character_survives_special_keys_and_invalid_bytes_keep_following_input(self):
        self.screen.getch.side_effect = [0xC3, curses.KEY_RESIZE, 0xA9, 0xFF, ord("x")]
        self.assertEqual(self.events.read_or_wait(time.monotonic()), curses.KEY_RESIZE)
        self.assertEqual(self.events.read_or_wait(time.monotonic()), "é")
        self.assertEqual(self.events.read_or_wait(time.monotonic()), "�")
        self.assertEqual(self.events.read_or_wait(time.monotonic()), "x")

    def test_fragmented_unicode_and_action_wakeup_on_real_curses_pty(self):
        # Report events out of band so curses output/echo cannot fake decoded keys.
        master, slave = pty.openpty()
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        self.addCleanup(os.close, master)
        program = r"""
import curses, json, socket, sys, time
from tmux_workspaces.events import InputEvents
channel = socket.socket(fileno=int(sys.argv[1]))
channel.setblocking(False)
def run(screen):
    screen.keypad(True)
    screen.timeout(0)
    events = InputEvents(screen, channel)
    channel.send(b'ready')
    while True:
        try:
            action = channel.recv(32)
        except BlockingIOError:
            action = None
        if action:
            channel.send(b'action:' + action)
        key = events.read_or_wait(time.monotonic() + 10)
        if key is not None:
            channel.send(json.dumps(key).encode())
            if key == '\x04':
                break
curses.wrapper(run)
"""
        try:
            process = subprocess.Popen(
                [sys.executable, "-c", program, str(child.fileno())],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=os.environ | {"TERM": "xterm-256color", "LANG": "en_US.UTF-8"},
                pass_fds=(child.fileno(),),
                cwd=Path(__file__).resolve().parents[2],
            )
        finally:
            os.close(slave)

        def receive():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                ready, _, _ = select.select([parent, master], [], [], 0.05)
                if parent in ready:
                    return parent.recv(4096)
                if master in ready:
                    os.read(master, 65536)
            self.fail("real curses fixture did not report an event")

        try:
            self.assertEqual(receive(), b"ready")
            os.write(master, b"\xc3")
            time.sleep(0.05)
            parent.send(b"shortcut")
            # The partial code point must not block processing the action socket.
            self.assertEqual(receive(), b"action:shortcut")
            os.write(master, b"\xa9")
            self.assertEqual(receive(), b'"\\u00e9"')
            os.write(master, "界".encode())
            self.assertEqual(receive(), b'"\\u754c"')
            os.write(master, b"\x04")
            self.assertEqual(receive(), b'"\\u0004"')
            deadline = time.monotonic() + 5
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    with contextlib.suppress(OSError):
                        os.read(master, 65536)
            self.assertEqual(process.poll(), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_action_readiness_wakes_wait_without_consuming_acknowledged_protocol(self):
        self.sender.send(b"next-tab")
        observed = []
        original_select = select.select

        def select_and_record(*args):
            result = original_select(*args)
            observed.extend(result[0])
            return result

        with patch("tmux_workspaces.events.select.select", side_effect=select_and_record):
            self.assertIsNone(self.events.read_or_wait(time.monotonic() + 10))
        self.assertEqual(observed, [self.receiver])
        # Only Actions.pending may consume or acknowledge the command.
        self.assertEqual(self.receiver.recv(64), b"next-tab")

    def test_terminal_readiness_does_not_steal_curses_bytes(self):
        os.write(self.writer, b"mouse or UTF-8 input")
        self.assertIsNone(self.events.read_or_wait(time.monotonic() + 10))
        self.assertEqual(os.read(self.reader, 64), b"mouse or UTF-8 input")

    def test_idle_wait_is_bounded_for_ncurses_resize_and_metadata_deadlines(self):
        with (
            patch("tmux_workspaces.events.time.monotonic", return_value=10),
            patch("tmux_workspaces.events.select.select") as waiting,
        ):
            self.events.read_or_wait(99)
            self.assertEqual(waiting.call_args.args[-1], RESIZE_CHECK_SECONDS)
            self.events.read_or_wait(10.04)
            self.assertAlmostEqual(waiting.call_args.args[-1], 0.04)
            self.events.read_or_wait(9)
            self.assertEqual(waiting.call_args.args[-1], 0)


if __name__ == "__main__":
    unittest.main()
