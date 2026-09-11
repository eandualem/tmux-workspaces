"""Read-only external session attachment clients and recursive-host protection."""

import os
import shlex
import subprocess
import time
import uuid

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
            rows, cols = struct.unpack("HHHH", fcntl.ioctl(1, termios.TIOCGWINSZ, b"\0" * 8))[:2]
        except OSError:
            rows, cols = 24, 80
        sequence = ""
        if color.startswith("#") and len(color) == 7:
            r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
            sequence = f"\033[38;2;{r};{g};{b}m"
        elif color.startswith("colour") and color[6:].isdigit():
            sequence = f"\033[38;5;{int(color[6:])}m"
        if args.vertical:
            # One thin line down a one-column pane: the same weight as the rule
            # between stacked panes, rather than a filled column of color.
            body = "".join(f"\033[{row};1H│" for row in range(1, max(1, rows) + 1))
        else:
            body = "\033[H" + "─" * max(0, cols)
        sys.stdout.write("\033[?25l\033[2J" + sequence + body + "\033[0m")
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


# Grouped sessions the viewer creates for its own attach clients carry this
# prefix, so tools and tests can tell them from the sessions people run.
GROUPED_PREFIX = "tw-"


def grouped_session_name() -> str:
    """A name of the viewer's own for one grouped attach session."""
    return f"{GROUPED_PREFIX}{os.getpid():x}-{uuid.uuid4().hex[:6]}"


def grouped_attach_command(
    source_socket: str,
    target: str,
    name: str | None = None,
    options: list[list[str]] | None = None,
) -> list[str]:
    """Attach to an external session through a grouped session of this viewer's own.

    A grouped session shares the target's windows but carries its own session
    options, so its status line can be turned off for this pane without
    touching the session the user (or an agent) is running, and the row it
    occupied returns to the program inside. tmux gives a grouped session the
    server's defaults, not the target's settings, so the target's own session
    options (``mouse`` above all) are copied first and the status line turned
    off after them. The grouped session is destroyed as soon as this client
    detaches; the target is never modified.
    """
    name = name or grouped_session_name()
    command = ["tmux", "-S", source_socket, "new-session", "-E", "-t", target, "-s", name]
    for option in [*(options or []), ["status", "off"], ["destroy-unattached", "on"]]:
        command += [";", "set-option", "-t", name, *option]
    return command


def target_session_options(source_socket: str, target: str) -> list[list[str]]:
    """The options set on the target session itself, as ``set-option`` words."""
    listed = subprocess.run(
        ["tmux", "-S", source_socket, "show-options", "-t", target],
        env=clean_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    ).stdout
    options = []
    for line in listed.splitlines():
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        if len(words) >= 2 and words[0] not in ("status", "destroy-unattached"):
            options.append(words)
    return options


def session_exists(source_socket: str, target: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "-S", source_socket, "has-session", "-t", target],
            env=clean_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).returncode
        == 0
    )


def run_grouped_attachment(source_socket: str, target: str) -> None:
    """Run one grouped attach client until it detaches or the target ends.

    Sessions in a group keep each other's windows alive, so a grouped session
    left standing after the target was killed would keep the target's shells
    running inside the viewer. The client is therefore watched: once the
    target is gone, the grouped session is killed too, the client returns and
    the pane shows the session as offline like any other attachment.
    """
    name = grouped_session_name()
    options = target_session_options(source_socket, target)
    command = grouped_attach_command(source_socket, target, name, options)
    with subprocess.Popen(command, env=clean_env()) as client:
        while client.poll() is None:
            time.sleep(1)
            if client.poll() is None and not session_exists(source_socket, target):
                subprocess.run(
                    ["tmux", "-S", source_socket, "kill-session", "-t", name],
                    env=clean_env(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
                try:
                    client.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    client.terminate()


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
        exists = session_exists(args.source_socket, target)
        if exists and attachment_hosts_viewer(args, target):
            current = "host"
            detail = (
                "This session hosts the viewer.\r\n\r\n"
                "Choose another session, or use Tab… → Return pane to shell."
            )
        elif exists:
            notice = ""
            if args.agent:
                run_grouped_attachment(args.source_socket, target)
            else:
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
