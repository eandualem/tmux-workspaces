"""Viewer actions travel to the sidebar, never through an agent's terminal."""

from __future__ import annotations

import contextlib
import re
import socket
import uuid
from pathlib import Path

SHORTCUTS = {
    "t": "new-tab",
    "v": "split-right",
    "%": "split-right",
    "h": "split-below",
    '"': "split-below",
    "a": "attach",
    "r": "rename-tab",
    "n": "next-tab",
    "p": "previous-tab",
    "o": "next-pane",
    "O": "previous-pane",
    "z": "focus",
    "w": "workspaces",
    "W": "new-workspace",
    "R": "rename-workspace",
    "]": "next-workspace",
    "[": "previous-workspace",
    "x": "close-pane",
    "&": "close-tab",
}

# Explicit codes stay stable when bindings are added. These are terminal input
# sequences, consumed by the viewer's private tmux server before reaching a shell.
# Ghostty supplies them with its csi keybinding action; other terminals can too.
DIRECT_SHORTCUTS = {
    "new-tab": ("super+t", "⌘T", 9001),
    "split-right": ("super+d", "⌘D", 9002),
    "split-below": ("super+shift+d", "⌘⇧D", 9003),
    "previous-pane": ("super+bracket_left", "⌘[", 9004),
    "next-pane": ("super+bracket_right", "⌘]", 9005),
    "previous-tab": ("super+shift+bracket_left", "⌘⇧[", 9006),
    "next-tab": ("super+shift+bracket_right", "⌘⇧]", 9007),
    "previous-workspace": ("super+alt+left", "⌘⌥←", 9008),
    "next-workspace": ("super+alt+right", "⌘⌥→", 9009),
    "new-workspace": ("super+shift+n", "⌘⇧N", 9010),
    "rename-tab": ("super+r", "⌘R", 9011),
    "rename-workspace": ("super+shift+r", "⌘⇧R", 9012),
    "focus": ("super+shift+enter", "⌘⇧Enter", 9013),
    "attach": ("super+shift+a", "⌘⇧A", 9014),
    "sidebar": ("super+b", "⌘B", 9015),
    "close-pane": ("super+w", "⌘W", 9016),
    "close-tab": ("super+shift+w", "⌘⇧W", 9017),
    **{f"select-tab-{n}": (f"super+{n}", f"⌘{n}", 9020 + n) for n in range(1, 10)},
    **{f"select-workspace-{n}": (f"super+alt+{n}", f"⌘⌥{n}", 9040 + n) for n in range(1, 10)},
}

ACTIONS = frozenset(SHORTCUTS.values()) | DIRECT_SHORTCUTS.keys() | {"quit"}


def mouse_action(action: str) -> tuple[int, int] | None:
    """Decode a bounded pane-relative mouse action without evaluating input."""
    match = re.fullmatch(r"mouse:left:([0-9]{1,5}):([0-9]{1,5})", action)
    if match:
        x, y = match.groups()
        if int(x) <= 65535 and int(y) <= 65535:
            return int(x), int(y)
    return None


def valid_action(action: str) -> bool:
    return (
        action in ACTIONS
        or mouse_action(action) is not None
        or re.fullmatch(r"attach-pane:[a-f0-9]{12}:[a-f0-9]{12}", action) is not None
    )


def direct_sequence(action: str) -> str:
    from .keymap import direct_sequence as sequence

    return sequence(action)


def ghostty_bindings() -> str:
    from .keymap import DEFAULT_KEYMAP

    return DEFAULT_KEYMAP.ghostty_bindings()


def send_action(path: str, action: str, *, wait: bool = False) -> None:
    if not valid_action(action):
        raise ValueError("Unknown viewer action")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
        reply_path = Path(path).parent / f".action-reply-{uuid.uuid4().hex}.sock" if wait else None
        try:
            sender.settimeout(10 if wait else 1)
            if reply_path:
                sender.bind(str(reply_path))
            sender.sendto(action.encode(), path)
            if wait and sender.recv(16) != b"applied":
                raise ValueError("Viewer did not acknowledge the shortcut")
        finally:
            if reply_path:
                reply_path.unlink(missing_ok=True)


class Actions:
    def __init__(self, path: str):
        self.path = path
        self.receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.receiver.bind(path)
        self.receiver.setblocking(False)

    def pending(self):
        while True:
            try:
                payload, sender = self.receiver.recvfrom(64)
                action = payload.decode()
            except BlockingIOError:
                return
            if valid_action(action):
                try:
                    yield action
                finally:
                    # A synchronous tmux binding waits until the sidebar has
                    # applied the action (including focus) before releasing input.
                    # Older callers without a return address stay compatible.
                    if sender:
                        with contextlib.suppress(OSError):
                            self.receiver.sendto(b"applied", sender)

    def close(self):
        self.receiver.close()
        Path(self.path).unlink(missing_ok=True)
