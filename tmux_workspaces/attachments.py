"""Read-only external session attachment clients and recursive-host protection."""

import os
import subprocess
import time

from .targets import session_target
from .tmux import Tmux, clean_env


def attachment_hosts_viewer(args, target: str) -> bool:
    if not args.host_socket or not args.host_pane:
        return False
    if os.path.realpath(args.host_socket) != os.path.realpath(args.source_socket):
        return False
    tmux = Tmux(args.source_socket)
    # Linked windows and grouped sessions can share a pane across session IDs.
    # Pane IDs are server-wide, so inspect every window in the target session.
    panes = tmux.run("list-panes", "-s", "-t", target, "-F", "#{pane_id}", check=False)
    return args.host_pane in panes.splitlines()


def rule_main(args) -> int:
    """Draw one horizontal rule across this pane, redrawn when its size changes.

    The pane is a one-row separator between two stacked panes. A whole row of
    the separator color would weigh more than the one-column separator beside
    two side-by-side panes, so a thin line is drawn instead.
    """
    import fcntl
    import struct
    import sys
    import termios

    def paint(color: str) -> None:
        try:
            cols = struct.unpack("HHHH", fcntl.ioctl(1, termios.TIOCGWINSZ, b"\0" * 8))[1]
        except OSError:
            cols = 80
        sequence = ""
        if color.startswith("#") and len(color) == 7:
            r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
            sequence = f"\033[38;2;{r};{g};{b}m"
        elif color.startswith("colour") and color[6:].isdigit():
            sequence = f"\033[38;5;{int(color[6:])}m"
        sys.stdout.write("\033[?25l\033[H" + sequence + "─" * max(0, cols) + "\033[0m")
        sys.stdout.flush()

    painted = None
    while True:
        try:
            size = fcntl.ioctl(1, termios.TIOCGWINSZ, b"\0" * 8)
        except OSError:
            size = None
        if size != painted:
            paint(args.color)
            painted = size
        time.sleep(0.5)


def leaf_main(args) -> int:
    name = args.agent or args.terminal
    if not name:
        print(
            "\033[2J\033[HCreate a terminal with the top +, or Ctrl-g then t.",
            flush=True,
        )
        while True:
            time.sleep(60)
    target = session_target(name)
    # Attaching must not update the external session's environment from this
    # filtered client (including removing its existing SSH agent socket).
    command = ["tmux", "-S", args.source_socket, "attach-session", "-E", "-t", target]
    # The display has just ensured ordinary sessions exist. Start their first
    # attachment directly; preserve host checks and the normal recovery loop if
    # that session disappears before the client connects. External attachments
    # retain the preflight below, including recursive-host protection.
    if args.terminal and not args.agent and not attachment_hosts_viewer(args, target):
        subprocess.run(command, env=clean_env(), stderr=subprocess.DEVNULL, check=False)
        time.sleep(1)
    notice = ""
    while True:
        exists = (
            subprocess.run(
                ["tmux", "-S", args.source_socket, "has-session", "-t", target],
                env=clean_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).returncode
            == 0
        )
        if exists and attachment_hosts_viewer(args, target):
            current = "host"
            detail = (
                "This session hosts the viewer.\r\n\r\n"
                "Choose another session, or use Tab… → Return pane to shell."
            )
        elif exists:
            notice = ""
            subprocess.run(command, env=clean_env(), check=False)
            time.sleep(1)
            continue
        else:
            current = "offline"
            detail = (
                "session offline.\r\n\r\n"
                "This pane is saved. It reconnects when the session returns."
            )
        if current != notice:
            print(
                "\033[2J\033[H" + name + " — " + detail,
                flush=True,
            )
            notice = current
        time.sleep(1)
