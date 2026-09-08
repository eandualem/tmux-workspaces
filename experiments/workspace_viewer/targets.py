"""Keep raw tmux session names separate from exact command targets."""

from __future__ import annotations


def valid_session(name: str) -> bool:
    # tmux interprets a colon as a session/window separator. Printable names may
    # otherwise include spaces, Unicode, or punctuation; never strip or rename them.
    return isinstance(name, str) and bool(name) and name.isprintable() and ":" not in name


def session_target(name: str) -> str:
    if not valid_session(name):
        raise ValueError("Invalid tmux session name")
    # The trailing colon matters: '=name' alone may be interpreted as a window.
    return "=" + name + ":"
