"""Wait for terminal input or action datagrams without sleeping through shortcuts."""

from __future__ import annotations

import codecs
import curses
import locale
import select
import socket
import time
from collections import deque

# ncurses can queue KEY_RESIZE internally without making stdin readable. Keep its
# native SIGWINCH handler and check that queue at least as often as before.
RESIZE_CHECK_SECONDS = 0.15


class InputEvents:
    def __init__(self, screen, receiver: socket.socket, terminal_fd: int = 0):
        self.screen, self.receiver, self.terminal_fd = screen, receiver, terminal_fd
        encoding = getattr(screen, "encoding", None) or locale.getencoding()
        self.decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self.keys: deque[str | int] = deque()

    def read_or_wait(self, deadline: float) -> str | int | None:
        """Read curses' own queue first; otherwise wait for either input channel.

        The screen must be nonblocking. Return after readiness without consuming
        descriptors: the caller handles pending actions before reading terminal
        input. Curses decodes mouse/special-key sequences. An incremental decoder
        retains text fragments: nonblocking get_wch can lose an incomplete UTF-8
        character when its continuation arrives in another terminal read.
        """
        while not self.keys:
            try:
                key = self.screen.getch()
            except curses.error:
                break
            if key < 0:
                break
            if key > 255:
                self.keys.append(key)
            else:
                self.keys.extend(self.decoder.decode(bytes([key])))
        if self.keys:
            return self.keys.popleft()
        timeout = min(RESIZE_CHECK_SECONDS, max(0.0, deadline - time.monotonic()))
        select.select([self.terminal_fd, self.receiver], [], [], timeout)
        return None
