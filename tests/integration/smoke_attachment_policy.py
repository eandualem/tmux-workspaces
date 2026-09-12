"""Auto-destroy source policies must not turn attachment into session deletion."""

import os
import sys

from tests.integration.support import FixtureResources, wait


def exercise(resources, policy, inherited=False, launcher=None):
    source = resources.server("source")
    source.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "sample",
        "-e",
        "HOME=" + str(resources.root),
        "-c",
        str(resources.root),
        "/bin/sh",
    )
    target = source.run("display-message", "-p", "-t", "=sample:", "#{session_id}")
    # Policies other than keep-last require an existing client before enabling
    # them. All source clients are owned by this fixture.
    owner = None
    if policy != "keep-last":
        owner = resources.client([], launcher=["tmux", "-S", source.socket, "attach", "-t", target])
        wait(
            owner,
            lambda: source.run("list-clients", "-F", "#{session_id}") == target,
            "source client missing",
        )
    try:
        source.run(
            "set-option", *(["-g"] if inherited else ["-t", target]), "destroy-unattached", policy
        )
    except RuntimeError as error:
        if policy.startswith("keep-") and "bad value" in str(error):
            print(f"SKIP: this tmux version does not support destroy-unattached {policy}")
            return
        raise
    original_options = source.run("show-options", "-t", target)
    original_globals = source.run("show-options", "-g")
    original_keys = source.run("list-keys")
    original_panes = source.run(
        "list-panes", "-s", "-t", target, "-F", "#{pane_id}|#{pane_pid}|#{pane_current_path}"
    )
    program = """
import sys
from tmux_workspaces.attachments import run_grouped_attachment
run_grouped_attachment(sys.argv[1], '=sample:')
"""
    client = resources.client([source.socket], launcher=launcher or [sys.executable, "-c", program])

    def attached():
        sessions = source.run("list-sessions", "-F", "#{session_id}")
        assert sessions == target, ("attachment changed source sessions", target, sessions)
        clients = source.run("list-clients", "-F", "#{session_id}").splitlines()
        return clients == [target] * (2 if owner else 1)

    wait(client, attached, "safe direct attachment missing")
    token = os.urandom(6).hex()
    marker = "POLICY_" + token
    client.type("printf 'POLICY_%s\\n' " + token + "\r")
    wait(
        client,
        lambda: marker in source.run("capture-pane", "-p", "-t", target),
        "input did not reach the preserved source shell",
    )
    if owner:
        owner.close()
        wait(
            client,
            lambda: source.run("list-clients", "-F", "#{session_id}") == target,
            "source did not survive the original client detaching",
        )
        token = os.urandom(6).hex()
        marker = "RETAINED_" + token
        client.type("printf 'RETAINED_%s\\n' " + token + "\r")
        wait(
            client,
            lambda: marker in source.run("capture-pane", "-p", "-t", target),
            "direct viewer did not keep the source usable after owner detached",
        )
        owner = resources.client([], launcher=["tmux", "-S", source.socket, "attach", "-t", target])
        wait(
            owner,
            lambda: len(source.run("list-clients", "-F", "#{session_id}").splitlines()) == 2,
            "original client did not return",
        )
    client.close()
    for _ in range(3):
        assert source.run("list-sessions", "-F", "#{session_id}") == target
        assert (
            source.run(
                "list-panes",
                "-s",
                "-t",
                target,
                "-F",
                "#{pane_id}|#{pane_pid}|#{pane_current_path}",
            )
            == original_panes
        )
    assert source.run("show-options", "-t", target) == original_options
    assert source.run("show-options", "-g") == original_globals
    assert source.run("list-keys") == original_keys
    print(
        f"PASS: {policy} ({'inherited' if inherited else 'local'}) attaches without grouping; "
        "source shells, options and bindings survive viewer close",
        flush=True,
    )


if __name__ == "__main__":
    for policy, inherited in (
        ("keep-last", False),
        ("keep-last", True),
        ("on", False),
        ("keep-group", False),
    ):
        with FixtureResources(prefix="tw-policy-") as resources:
            exercise(resources, policy, inherited)
