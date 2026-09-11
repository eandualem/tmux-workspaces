"""Ask the terminal for its background color, before the viewer enters tmux.

The viewer pads each pane with a blank column on either side, in the terminal's
own background, and hides tmux's border glyph beside that padding by painting
it in the same color. That needs the color, which only the terminal knows.
Most terminals answer the standard query (OSC 11); one that does not, or that
answers too slowly, simply gets no padding, and the viewer looks as it did.
"""

from __future__ import annotations

import os
import re
import select
import termios
import time

QUERY = b"\x1b]11;?\x1b\\"
_REPLY = re.compile(
    rb"\x1b\]11;(?:rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)|(#[0-9a-fA-F]{6}))"
)
_END = (b"\x1b\\", b"\x07")


def parse_reply(reply: bytes) -> str | None:
    """The `#rrggbb` a terminal's OSC 11 reply names, or None if it is not one.

    Terminals answer with `rgb:rrrr/gggg/bbbb` (4 hex digits per channel) or
    `rgb:rr/gg/bb`; the leading digits of each channel are the 8-bit value.
    """
    match = _REPLY.search(reply)
    if not match:
        return None
    if match.group(4):
        return match.group(4).decode().lower()
    channels = []
    for part in match.group(1, 2, 3):
        text = part.decode()
        channels.append(text[:2] if len(text) >= 2 else text * 2)
    return "#" + "".join(channels).lower()


def terminal_background(timeout: float = 0.25) -> str | None:
    """Query the controlling terminal once; None when it does not answer in time.

    The query goes to /dev/tty directly so a redirected stdin or stdout does not
    matter, and the terminal is read raw for at most ``timeout`` seconds. Any
    bytes read that are not the reply are lost; the window is short and this
    runs before the viewer shows anything, so nothing typed on purpose is
    consumed in practice.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    try:
        try:
            saved = termios.tcgetattr(fd)
        except termios.error:
            return None
        raw = list(saved)
        raw[3] &= ~(termios.ECHO | termios.ICANON)
        raw[6] = list(raw[6])
        raw[6][termios.VMIN], raw[6][termios.VTIME] = 0, 0
        try:
            termios.tcsetattr(fd, termios.TCSANOW, raw)
            os.write(fd, QUERY)
            reply = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                if not select.select([fd], [], [], remaining)[0]:
                    break
                chunk = os.read(fd, 256)
                if not chunk:
                    break
                reply += chunk
                if b"\x1b]11;" in reply and any(end in reply for end in _END):
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
    except (OSError, termios.error, ValueError):
        return None
    finally:
        os.close(fd)
    return parse_reply(reply)
