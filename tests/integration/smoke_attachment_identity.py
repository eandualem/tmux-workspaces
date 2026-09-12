"""A replaced session name must not keep an obsolete attachment group alive."""

import sys

from tests.integration.support import FixtureResources, wait
from tmux_workspaces.attachments import GROUPED_MARKER


def exercise(resources):
    source = resources.server()
    for name in ("sample", "untouched"):
        source.run(
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-s",
            name,
            "-e",
            "HOME=" + str(resources.root),
            "/bin/sh",
        )
    original_id = source.run("display-message", "-p", "-t", "=sample:", "#{session_id}")
    original_pane = source.run("display-message", "-p", "-t", "=sample:", "#{pane_id}")
    untouched_pid = source.run("display-message", "-p", "-t", "=untouched:", "#{pane_pid}")
    original_bindings = source.run("list-keys")
    probe_ready, replaced = resources.root / "probe.ready", resources.root / "replaced"
    # Pause the first liveness probe so replacement definitely happens before
    # it reads the source server. The grouped client otherwise runs unchanged.
    program = """
import sys, time
from pathlib import Path
from tmux_workspaces import attachments
ready, replaced = Path(sys.argv[2]), Path(sys.argv[3])
original_probe = attachments.session_exists
def probe(socket, target):
    ready.touch()
    deadline = time.monotonic() + 10
    while not replaced.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('fixture did not replace source session')
        time.sleep(0.02)
    return original_probe(socket, target)
attachments.session_exists = probe
attachments.TARGET_PROBE_SECONDS = 0.05
attachments.run_grouped_attachment(sys.argv[1], '=sample:')
"""
    client = resources.client(
        [source.socket, str(probe_ready), str(replaced)],
        launcher=[sys.executable, "-c", program],
    )

    def helpers():
        return source.run(
            "list-sessions",
            "-f",
            f"#{{==:#{{{GROUPED_MARKER}}},1}}",
            "-F",
            "#{session_name}",
        ).splitlines()

    wait(client, lambda: probe_ready.exists() and helpers(), "attachment did not reach its probe")
    helper = helpers()[0]
    assert source.run("list-panes", "-t", "=" + helper + ":", "-F", "#{pane_id}") == original_pane
    source.run("kill-session", "-t", original_id)
    source.run("new-session", "-d", "-s", "sample", "-e", "HOME=" + str(resources.root), "/bin/sh")
    replacement_id = source.run("display-message", "-p", "-t", "=sample:", "#{session_id}")
    replacement_pid = source.run("display-message", "-p", "-t", "=sample:", "#{pane_pid}")
    replacement_options = source.run("show-options", "-t", "=sample:")
    assert replacement_id != original_id
    replaced.touch()
    wait(client, lambda: client.process.poll() is not None, "obsolete attachment stayed alive")
    assert client.process.returncode == 0, client.output
    assert not helpers(), "obsolete helper survived target replacement"
    assert original_pane not in source.run("list-panes", "-a", "-F", "#{pane_id}").splitlines()
    assert source.run("display-message", "-p", "-t", "=sample:", "#{pane_pid}") == replacement_pid
    assert source.run("show-options", "-t", "=sample:") == replacement_options
    assert source.run("display-message", "-p", "-t", "=untouched:", "#{pane_pid}") == untouched_pid
    assert source.run("list-keys") == original_bindings
    print(
        "PASS: replaced source name ends obsolete grouped attachment; replacement survives",
        flush=True,
    )


if __name__ == "__main__":
    with FixtureResources(prefix="tw-identity-") as resources:
        exercise(resources)
