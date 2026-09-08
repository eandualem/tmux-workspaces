#!/usr/bin/env python3
"""Opt-in performance measurements on disposable tmux servers; JSON goes to stdout."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import platform
import pty
import resource
import select
import shlex
import shutil
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import time
from collections import Counter
from pathlib import Path

from tmux_workspaces import application, controls, model, persistence, shells, tmux

ROOT = Path(__file__).resolve().parents[1]
BUDGET = {"p50_ms": 150, "p95_ms": 250}


def repository_provenance(root=ROOT):
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=root).strip()
    tracked = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"], text=True, cwd=root
    ).splitlines()
    difference = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--"], cwd=root)
    return {
        "revision": revision,
        "tracked_dirty": bool(tracked),
        "tracked_changes": tracked,
        "tracked_diff_sha256": hashlib.sha256(difference).hexdigest() if tracked else None,
    }


def cleanup_all(callbacks):
    """Attempt every owned-resource cleanup, then surface all failures together."""
    errors = []
    for callback in callbacks:
        try:
            callback()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise BaseExceptionGroup("Benchmark fixture cleanup failed", errors)


def assert_unique_ack(capture, target, marker, targets):
    for other in targets:
        if other != target and marker in capture(other):
            raise RuntimeError("Input acknowledgement also reached another fixture shell")


def summary(values):
    """Nearest-rank p95, retaining counts so small samples cannot masquerade as tails."""
    ordered = sorted(values)
    if not ordered:
        return {"n": 0}
    return {
        "n": len(ordered),
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
    }


def clock_seconds(value):
    """Parse BSD ps elapsed CPU: [[days-]hours:]minutes:seconds.fraction."""
    days, value = value.split("-", 1) if "-" in value else ("0", value)
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return int(days) * 86400 + total


def linux_stat(value, ticks):
    # comm can contain spaces and ')' characters; fields follow its last ')'.
    fields = value.rsplit(")", 1)[1].split()
    return int(fields[1]), sum(int(fields[i]) for i in (11, 12, 13, 14)) / ticks


def traced_command(line):
    arguments = line.split()
    while arguments:
        item = arguments.pop(0)
        if item in {"-S", "-L", "-f", "-c", "-T"} and arguments:
            arguments.pop(0)
        elif not item.startswith("-"):
            return item
    return "unknown"


def processes():
    if sys.platform == "darwin":
        lines = subprocess.check_output(
            ["ps", "-S", "-axo", "pid=,ppid=,time="], text=True
        ).splitlines()
        return {
            int(pid): (int(parent), clock_seconds(clock))
            for pid, parent, clock in (line.split() for line in lines)
        }
    if sys.platform.startswith("linux"):
        ticks = os.sysconf("SC_CLK_TCK")
        table = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                table[int(entry.name)] = linux_stat((entry / "stat").read_text(), ticks)
            except (OSError, ValueError, IndexError):
                continue  # A process can exit during enumeration.
        return table
    raise RuntimeError("CPU accounting currently supports macOS and Linux only")


def descendants(table, roots):
    owned = set(roots)
    while True:
        children = {pid for pid, (parent, _) in table.items() if parent in owned}
        if children <= owned:
            return owned & table.keys()
        owned |= children


def cpu_delta(start, end, roots):
    before, after = descendants(start, roots), descendants(end, roots)
    # Summed self + waited-child counters transfer exited child CPU into its parent.
    # Persistent descendants are counted once; no recursive ancestor sum is added.
    elapsed = sum(end[pid][1] for pid in after) - sum(start[pid][1] for pid in before)
    stable = before == after and set(roots) <= before
    return {
        "cpu_seconds": max(0, elapsed) if stable and elapsed >= 0 else None,
        "stable_process_set": stable,
        "process_count": len(before),
        "accounting_valid": stable and elapsed >= 0,
    }


def observer_cpu():
    # Viewer launcher is alive during sampling; it is not in RUSAGE_CHILDREN yet.
    return sum(
        usage.ru_utime + usage.ru_stime
        for usage in (
            resource.getrusage(resource.RUSAGE_SELF),
            resource.getrusage(resource.RUSAGE_CHILDREN),
        )
    )


class Terminal:
    def __init__(self, library, source, env, cols, rows):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        try:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "run"),
                    "--data-dir",
                    str(library),
                    "--source-socket",
                    str(source),
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                start_new_session=True,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self.received = 0
        self.first_output = None
        self.tail = b""

    def pump(self, seconds=0.005):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if select.select([self.master], [], [], max(0, until - time.monotonic()))[0]:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    return
                if not data:
                    return
                if self.first_output is None:
                    self.first_output = time.monotonic()
                self.received += len(data)
                self.tail = (self.tail + data)[-4096:]

    def send(self, data):
        os.write(self.master, data.encode())

    def resize(self, cols, rows):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.killpg(self.process.pid, signal.SIGWINCH)

    def close(self):
        def stop_process():
            if self.process.poll() is None:
                self.process.terminate()
                deadline = time.monotonic() + 10
                while self.process.poll() is None and time.monotonic() < deadline:
                    self.pump(0.02)
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait()

        def close_pty():
            if self.master is not None:
                descriptor, self.master = self.master, None
                os.close(descriptor)

        cleanup_all([stop_process, close_pty])


class Fixture:
    def __init__(self, root, options, count):
        self.root, self.options, self.count = root, options, count
        self.clients, self.views = [], []
        self.tmux_calls = 0
        self.tmux_seconds = 0
        self.real_tmux = shutil.which("tmux")
        self.library, self.source = root / "library", root / "source.sock"
        self.trace = root / "commands.log"
        self.env = tmux.clean_env() | {
            "TERM": "xterm-256color",
            "LANG": "en_US.UTF-8",
            "SHELL": "/bin/sh",
            "HOME": str(root),  # Never source the operator's shell startup files.
        }
        if options.trace_commands:
            bindir = root / "bin"
            bindir.mkdir()
            wrapper = bindir / "tmux"
            wrapper.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> " + shlex.quote(str(self.trace)) + "\n"
                "exec " + shlex.quote(self.real_tmux) + ' "$@"\n'
            )
            wrapper.chmod(0o755)
            self.env["PATH"] = str(bindir) + os.pathsep + self.env.get("PATH", "")
        store = persistence.Store(self.library)
        model = store.load()
        self.spaces = []
        for index in range(2):
            if index:
                model.add_workspace(f"Purpose {index + 1}")
                model.add_tab()
            model.space["name"] = f"Purpose {index + 1}"
            for tab_index in range(2):
                if tab_index:
                    model.add_tab()
                model.tab["name"] = f"view-{index + 1}-{tab_index + 1}"
                level = 0
                while len(self.leaves(model.tab)) < count:
                    for pane in list(self.leaves(model.tab)):
                        model.tab["focus"] = pane["id"]
                        model.split("right" if level == 0 else "below", str(root))
                    level += 1
                for pane in self.leaves(model.tab):
                    pane["cwd"] = str(root)
            model.space["selected"] = model.space["tabs"][0]["id"]
            self.spaces.append(model.space)
        model.state["selected"] = self.spaces[0]["id"]
        store.save(model)
        store.close()
        self.shell = application.socket_path(self.library, "terminals")

    def leaves(self, tab):
        return list(model.leaves(tab["tree"]))

    def tmux(self, socket, *arguments, check=True):
        before = time.monotonic()
        result = subprocess.run(
            [self.real_tmux, "-S", str(socket), *arguments],
            text=True,
            capture_output=True,
            timeout=5,
            env=self.env,
        )
        self.tmux_calls += 1
        self.tmux_seconds += time.monotonic() - before
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()

    def wait(self, predicate, description):
        deadline = time.monotonic() + self.options.timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return time.monotonic()
            except RuntimeError as exc:
                last_error = str(exc)
            for client in self.clients:
                if client.process.poll() is not None:
                    raise RuntimeError(
                        "fixture viewer exited: " + client.tail.decode(errors="replace")
                    )
                client.pump(self.options.poll_ms / 1000)
        raise RuntimeError(description + (": " + last_error if last_error else ""))

    def start(self):
        for _ in range(self.options.viewers):
            client = Terminal(
                self.library, self.source, self.env, self.options.cols, self.options.rows
            )
            self.clients.append(client)

            def manifest(client=client):
                for path in (self.library / "windows").glob("*/runtime.json"):
                    try:
                        value = json.loads(path.read_text())
                        if value["pid"] == client.process.pid:
                            return value
                    except (OSError, ValueError):
                        pass
                return None

            self.wait(manifest, "viewer manifest missing")
            self.views.append(manifest()["viewer_socket"])
            self.wait(
                lambda: self.ready(self.spaces[0]["tabs"][0], len(self.views) - 1),
                "initial view not ready",
            )
        # Warm every tab so cold shell creation is not silently part of navigation.
        for space_index, space in enumerate(self.spaces):
            self.navigate("shortcut", "workspace", space_index, space["tabs"][0])
            for tab_index, tab in enumerate(space["tabs"]):
                self.navigate("shortcut", "tab", tab_index, tab)
            self.navigate("shortcut", "tab", 0, space["tabs"][0])
        self.navigate("shortcut", "workspace", 0, self.spaces[0]["tabs"][0])
        self.navigate("shortcut", "tab", 0, self.spaces[0]["tabs"][0])
        self.drain(self.options.warmup)
        self.shell_identities = self.identities()

    def identities(self):
        return set(
            self.tmux(
                self.shell, "list-panes", "-a", "-F", "#{session_name}|#{pane_id}|#{pane_pid}"
            ).splitlines()
        )

    def drain(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            for client in self.clients:
                client.pump(min(0.02, max(0, until - time.monotonic())))

    def ready(self, tab, view_index=0):
        rows = self.tmux(
            self.views[view_index],
            "list-panes",
            "-F",
            "#{@viewer_tab_id}|#{@viewer_leaf_id}|#{pane_tty}",
        ).splitlines()
        actual = {tuple(row.split("|")[:2]): row.split("|")[2] for row in rows}
        expected = {(tab["id"], pane["id"]) for pane in self.leaves(tab)}
        if not expected <= actual.keys() or len(rows) != len(expected) + 1:
            return False
        # Match the source client tty to this display pane, not another viewer
        # that happens to be attached to the same ordinary shell.
        clients = set(
            self.tmux(
                self.shell, "list-clients", "-F", "#{session_name}|#{client_tty}"
            ).splitlines()
        )
        return all(
            shells.Shells.name(pane) + "|" + actual[(tab["id"], pane["id"])] in clients
            for pane in self.leaves(tab)
        )

    def navigate(self, method, kind, index, tab):
        client, view = self.clients[0], self.views[0]
        if method == "shortcut":
            event = controls.direct_sequence(f"select-{kind}-{index + 1}")
        else:
            lines = self.tmux(view, "capture-pane", "-p", "-t", "%0").splitlines()
            label = tab["name"] if kind == "tab" else f"[ {index + 1} ]"
            row = next(i for i, line in enumerate(lines) if label in line)
            column = lines[row].index(label) + 1
            event = f"\x1b[<0;{column};{row + 1}M\x1b[<0;{column};{row + 1}m"
        self.drain(0.02)
        client.first_output = None
        initial_bytes = client.received
        calls, cost = self.tmux_calls, self.tmux_seconds
        trace_offset = self.trace.stat().st_size if self.trace.exists() else 0
        before = time.monotonic()
        client.send(event)
        ready_at = self.wait(lambda: self.ready(tab), "shell clients not ready after navigation")
        token = os.urandom(8).hex()
        # Splitting the output marker prevents terminal echo from faking acknowledgement.
        client.send("printf 'BENCH_%s\\n' " + token + "\r")
        target = "=" + shells.Shells.name({"id": tab["focus"]}) + ":"
        marker = "BENCH_" + token
        routed_at = self.wait(
            lambda: marker in self.tmux(self.shell, "capture-pane", "-p", "-t", target),
            "input acknowledgement did not reach the selected shell",
        )
        # Acknowledge only the selected shell. Wrong-shell input is a correctness failure.
        self.drain(0.02)
        result = {
            "method": method,
            "kind": kind,
            "panes": self.count,
            "target": [index + 1, tab["name"]],
            "shell_clients_ready_ms": (ready_at - before) * 1000,
            "routing_probe_sent_ms": (ready_at - before) * 1000,
            "routed_ack_ms": (routed_at - before) * 1000,
            "first_pty_output_ms": (
                (client.first_output - before) * 1000 if client.first_output else None
            ),
            "pty_bytes": client.received - initial_bytes,
            "observer_tmux_calls": self.tmux_calls - calls,
            "observer_tmux_ms": (self.tmux_seconds - cost) * 1000,
        }
        if self.trace.exists():
            with self.trace.open() as stream:
                stream.seek(trace_offset)
                result["application_tmux_invocations"] = len(stream.readlines())
        self.drain(self.options.settle)
        targets = [
            "=" + name + ":"
            for name in self.tmux(self.shell, "list-sessions", "-F", "#{session_name}").splitlines()
        ]
        assert_unique_ack(
            lambda other: self.tmux(self.shell, "capture-pane", "-p", "-t", other),
            target,
            marker,
            targets,
        )
        result["routed_ack_unique"] = True
        return result

    def helper_import_baseline(self):
        values = []
        for _ in range(5):
            before = time.monotonic()
            subprocess.run(
                [sys.executable, "-c", "import tmux_workspaces.cli"],
                cwd=ROOT,
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10,
            )
            values.append((time.monotonic() - before) * 1000)
        return {
            "raw_ms": values,
            "summary": summary(values),
            "scope": "fresh interpreter + CLI imports + exit; no action or tmux work",
        }

    def idle(self):
        roots = [client.process.pid for client in self.clients]
        roots += [
            int(self.tmux(socket, "display-message", "-p", "#{pid}"))
            for socket in [*self.views, self.shell]
        ]
        result = []
        for _ in range(self.options.idle_samples):
            observer_start = observer_cpu()
            start = processes()
            before = time.monotonic()
            self.drain(self.options.idle_seconds)
            elapsed = time.monotonic() - before
            end = processes()
            sample = cpu_delta(start, end, roots)
            sample.update(
                elapsed_seconds=elapsed,
                observer_cpu_seconds=observer_cpu() - observer_start,
                cpu_percent_one_core=(
                    sample["cpu_seconds"] / elapsed * 100
                    if sample["cpu_seconds"] is not None
                    else None
                ),
            )
            result.append(sample)
        return result

    def extra_scenarios(self):
        client, view = self.clients[0], self.views[0]
        tab = self.spaces[0]["tabs"][0]
        resize = []
        for cols in (self.options.cols - 10, self.options.cols):
            before = time.monotonic()
            client.resize(cols, self.options.rows)
            ended = self.wait(
                lambda cols=cols: (
                    self.tmux(view, "display-message", "-p", "#{window_width}") == str(cols)
                    and self.ready(tab)
                ),
                "layout did not fit after resize",
            )
            resize.append({"columns": cols, "shell_clients_ready_ms": (ended - before) * 1000})
            self.drain(self.options.settle)

        def create_source():
            self.tmux(
                str(self.source),
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                "bench-source",
                "-c",
                str(self.root),
                "/bin/sh -i",
            )

        create_source()
        client.send(controls.direct_sequence("attach"))

        def chooser_row():
            lines = self.tmux(view, "capture-pane", "-p", "-t", "%0").splitlines()
            return next(
                (
                    (line.index("bench-source") + 1, index + 1)
                    for index, line in enumerate(lines)
                    if "bench-source" in line
                ),
                None,
            )

        self.wait(chooser_row, "disposable attachment was not listed")
        x, y = chooser_row()
        client.send(f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m")
        self.wait(
            lambda: (
                self.tmux(
                    str(self.source),
                    "display-message",
                    "-p",
                    "-t",
                    "=bench-source:",
                    "#{session_attached}",
                )
                != "0"
            ),
            "disposable session was not attached",
        )
        attached_pane = next(
            line.split("|")[0]
            for line in self.tmux(
                view, "list-panes", "-F", "#{pane_id}|#{@viewer_agent}"
            ).splitlines()
            if line.endswith("|bench-source")
        )
        self.drain(self.options.settle)
        before = time.monotonic()
        self.tmux(str(self.source), "kill-server")
        offline = self.wait(
            lambda: "session offline" in self.tmux(view, "capture-pane", "-p", "-t", attached_pane),
            "offline attachment status missing",
        )
        before_online = time.monotonic()
        create_source()
        online = self.wait(
            lambda: (
                int(
                    self.tmux(
                        str(self.source),
                        "display-message",
                        "-p",
                        "-t",
                        "=bench-source:",
                        "#{session_attached}",
                    )
                    or "0"
                )
                > 0
            ),
            "recreated disposable session did not reconnect",
        )
        return {
            "resize": resize,
            "attachment_status": {
                "offline_content_notice_ms": (offline - before) * 1000,
                "reconnect_shell_clients_ms": (online - before_online) * 1000,
                "samples": 1,
            },
        }

    def close(self):
        callbacks = [client.close for client in self.clients]
        for socket in [*self.views, self.shell, str(self.source)]:
            callbacks.extend(
                [
                    lambda socket=socket: self.tmux(socket, "kill-server", check=False),
                    lambda socket=socket: Path(str(socket) + ".viewer-lock").unlink(
                        missing_ok=True
                    ),
                ]
            )
        cleanup_all(callbacks)


def run(options):
    provenance = repository_provenance()
    report = {
        "provenance": provenance,
        "acceptance_eligible": not provenance["tracked_dirty"] and not options.trace_commands,
        "schema": 1,
        "environment": {
            "system": platform.system(),
            "release": platform.release(),
            "machine_class": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "revision": provenance["revision"],
            "python": platform.python_version(),
            "tmux": subprocess.check_output(["tmux", "-V"], text=True).strip(),
            "terminal": "xterm-256color",
            "dimensions": [options.cols, options.rows],
        },
        "configuration": vars(options),
        "budget": BUDGET,
        "measurement": {
            "navigation": "event to clients attached; then PTY command to shell acknowledgement",
            "paint": "first PTY bytes; native paint and complete-frame latency are not measured",
            "cpu": "owned tree self + reaped children; stable PIDs required; one core = 100%",
            "observer": "separate self + waited-child CPU; observer runs tmux readiness queries",
            "trace": "optional wrapper perturbs startup; never compare traced to untraced runs",
            "polling": "configured pump interval per viewer plus synchronous tmux query costs",
        },
        "workloads": [],
    }
    for count in options.panes:
        print(f"Measuring {count} panes, {options.viewers} viewer(s)", file=sys.stderr, flush=True)
        with tempfile.TemporaryDirectory(prefix="tw-bench-", dir="/tmp") as temporary:
            fixture = Fixture(Path(temporary), options, count)
            try:
                fixture.start()
                workload = {
                    "panes": count,
                    "idle": fixture.idle(),
                    "navigation": [],
                    "helper_import_baseline": fixture.helper_import_baseline(),
                }
                for method in ("click", "shortcut"):
                    for kind in ("tab", "workspace"):
                        for sample in range(options.samples):
                            index = (sample + 1) % 2
                            tab = fixture.spaces[index if kind == "workspace" else 0]["tabs"][
                                index if kind == "tab" else 0
                            ]
                            workload["navigation"].append(
                                fixture.navigate(method, kind, index, tab)
                            )
                        # Return both workspace selections to the first tab for the next group.
                        for index in (1, 0):
                            fixture.navigate(
                                "shortcut",
                                "workspace",
                                index,
                                fixture.spaces[index]["tabs"][0],
                            )
                            fixture.navigate("shortcut", "tab", 0, fixture.spaces[index]["tabs"][0])
                workload["summaries"] = {}
                for method in ("click", "shortcut"):
                    for kind in ("tab", "workspace"):
                        rows = [
                            row
                            for row in workload["navigation"]
                            if row["method"] == method and row["kind"] == kind
                        ]
                        workload["summaries"][method + "_" + kind] = {
                            metric: summary([row[metric] for row in rows])
                            for metric in ("shell_clients_ready_ms", "routed_ack_ms")
                        }
                workload["additional_scenarios"] = fixture.extra_scenarios()
                workload["ordinary_shell_identities_preserved"] = (
                    fixture.identities() == fixture.shell_identities
                )
                if not workload["ordinary_shell_identities_preserved"]:
                    raise RuntimeError("ordinary shell identity changed during benchmark")
                if fixture.trace.exists():
                    workload["traced_total_invocations"] = len(
                        fixture.trace.read_text().splitlines()
                    )
                    workload["traced_commands"] = dict(
                        Counter(
                            traced_command(line) for line in fixture.trace.read_text().splitlines()
                        )
                    )
                report["workloads"].append(workload)
            finally:
                fixture.close()
    return report


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panes", nargs="+", type=int, choices=(1, 4, 8), default=[1, 4, 8])
    parser.add_argument("--viewers", type=int, choices=(1, 2), default=1)
    parser.add_argument(
        "--samples", type=int, default=20, help="per navigation method/kind, even count"
    )
    parser.add_argument("--idle-samples", type=int, default=3)
    parser.add_argument("--idle-seconds", type=float, default=30)
    parser.add_argument("--warmup", type=float, default=2)
    parser.add_argument("--settle", type=float, default=0.1)
    parser.add_argument("--poll-ms", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--cols", type=int, default=160)
    parser.add_argument("--rows", type=int, default=38)
    parser.add_argument("--trace-commands", action="store_true")
    options = parser.parse_args()
    durations = (
        options.idle_seconds,
        options.poll_ms,
        options.timeout,
        options.warmup,
        options.settle,
    )
    if not all(math.isfinite(value) for value in durations):
        parser.error("durations must be finite")
    if options.samples < 2 or options.samples % 2:
        parser.error("--samples must be a positive even number, at least 2")
    if options.idle_samples < 1 or min(options.idle_seconds, options.poll_ms, options.timeout) <= 0:
        parser.error("idle sample count, duration, polling and timeout must be positive")
    if min(options.warmup, options.settle) < 0:
        parser.error("warmup and settle must not be negative")
    if options.cols < 140 or options.rows < 34:
        parser.error("benchmark requires at least 140 columns and 34 rows for full layouts")
    return options


if __name__ == "__main__":
    print(json.dumps(run(arguments()), indent=2))
