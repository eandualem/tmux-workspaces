"""Disposable loopback OpenSSH host; never reads user keys or starts a service."""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

from tests.integration.support import Client


class SshHost:
    """A fixture-only public-key host with one forced viewer command.

    The caller owns a fresh scratch root. Only a loopback high port is opened;
    keys, known_hosts, config, home and logs live below that root. Client options
    explicitly exclude agents, user config and multiplexed existing connections.
    """

    def __init__(self, root: Path, viewer_args: list[str]):
        self.root = root
        self.root.mkdir(mode=0o700)
        self.home = root / "home"
        self.home.mkdir(mode=0o700)
        self.process: subprocess.Popen | None = None
        self.log = None
        self.clients: list[Client] = []
        self.ssh = shutil.which("ssh")
        self.sshd = shutil.which("sshd") or "/usr/sbin/sshd"
        self.keygen = shutil.which("ssh-keygen")
        if not self.ssh or not self.keygen or not Path(self.sshd).is_file():
            raise RuntimeError("SSH smoke requires OpenSSH client, ssh-keygen and sshd")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.username = pwd.getpwuid(os.getuid()).pw_name
        self.viewer_args = viewer_args

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note(
                    "SSH cleanup also failed:\n"
                    + "".join(traceback.format_exception(cleanup, chain=False))
                )
            raise

    def __exit__(self, _type, error, _traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(
                "SSH cleanup also failed:\n"
                + "".join(traceback.format_exception(cleanup, chain=False))
            )

    def start(self) -> None:
        for name in ("host_key", "client_key"):
            subprocess.run(
                [self.keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(self.root / name)],
                check=True,
                capture_output=True,
                timeout=10,
            )
        host_key = (self.root / "host_key.pub").read_text().split()[:2]
        (self.root / "known_hosts").write_text(f"[127.0.0.1]:{self.port} {' '.join(host_key)}\n")
        (self.root / "authorized_keys").write_text((self.root / "client_key.pub").read_text())
        (self.root / "authorized_keys").chmod(0o600)
        runner = self.root / "viewer.py"
        entry = Path(__file__).resolve().parents[2] / "run"
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.home),
            "ZDOTDIR": str(self.home),
            "SHELL": "/bin/sh",
            "LANG": "en_US.UTF-8",
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_DATA_HOME": str(self.home / "data"),
        }
        runner.write_text(
            "import json, os, sys\n"
            f"env = {env!r}\n"
            "env.update({key: os.environ[key] for key in "
            "('TERM', 'SSH_CONNECTION', 'SSH_TTY') if key in os.environ})\n"
            f"with open({str(self.root / 'connection.json')!r}, 'w') as stream:\n"
            "    json.dump({'connection': env.get('SSH_CONNECTION'), "
            "'tty': env.get('SSH_TTY'), 'term': env.get('TERM')}, stream)\n"
            f"os.execve({sys.executable!r}, "
            f"{[sys.executable, str(entry), *self.viewer_args]!r}, env)\n"
        )
        # SSH's login shell may read .zshenv before the forced command. HOME and
        # ZDOTDIR are set in the daemon too, then the runner applies its allowlist.
        config = [
            f"Port {self.port}",
            "ListenAddress 127.0.0.1",
            f"HostKey {self.root / 'host_key'}",
            f"PidFile {self.root / 'sshd.pid'}",
            f"AuthorizedKeysFile {self.root / 'authorized_keys'}",
            f"AllowUsers {self.username}",
            "AuthenticationMethods publickey",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "UsePAM no",
            "PermitRootLogin prohibit-password",
            "PermitUserRC no",
            "PermitUserEnvironment no",
            "DisableForwarding yes",
            "PermitTTY yes",
            "PrintMotd no",
            "PrintLastLog no",
            # The keys are under a fresh 0700 temp directory, not an account home.
            # No normal sshd configuration or filesystem modes are modified.
            "StrictModes no",
            "LogLevel VERBOSE",
            f"SetEnv HOME={self.home} ZDOTDIR={self.home}",
            "ForceCommand " + shlex.join([sys.executable, str(runner)]),
        ]
        path = self.root / "sshd_config"
        path.write_text("\n".join(config) + "\n")
        validation = subprocess.run(
            [self.sshd, "-t", "-f", str(path)], capture_output=True, text=True, timeout=10
        )
        if validation.returncode:
            raise RuntimeError("Private sshd preflight failed: " + validation.stderr.strip())
        self.log = (self.root / "sshd.log").open("wb")
        self.process = subprocess.Popen(
            [self.sshd, "-D", "-e", "-f", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=self.log,
            start_new_session=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Private sshd exited: " + self.diagnostics())
            if "Server listening on 127.0.0.1" in self.diagnostics():
                return
            time.sleep(0.02)
        raise RuntimeError("Private sshd did not start: " + self.diagnostics())

    def diagnostics(self) -> str:
        return (self.root / "sshd.log").read_text(errors="replace")[-4000:]

    def client(self, **kwargs) -> Client:
        args = [
            self.ssh,
            "-F",
            "/dev/null",
            "-tt",
            "-p",
            str(self.port),
            "-i",
            str(self.root / "client_key"),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(self.root / "known_hosts"),
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "EscapeChar=none",
            "-o",
            "LogLevel=ERROR",
            f"{self.username}@127.0.0.1",
        ]
        client = Client([], launcher=args, **kwargs)
        self.clients.append(client)
        return client

    def connection(self) -> dict:
        return json.loads((self.root / "connection.json").read_text())

    def close(self) -> None:
        errors = []
        clients, self.clients = self.clients, []
        for client in reversed(clients):
            try:
                client.close()
            except BaseException as error:
                errors.append(error)
        if self.process is not None:
            process = self.process
            try:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except BaseException:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    raise
            except BaseException as error:
                errors.append(error)
            finally:
                if process.poll() is not None:
                    self.process = None
        if self.log is not None:
            try:
                self.log.close()
            except BaseException as error:
                errors.append(error)
            finally:
                self.log = None
        for name in (
            "client_key",
            "client_key.pub",
            "host_key",
            "host_key.pub",
            "authorized_keys",
        ):
            try:
                (self.root / name).unlink(missing_ok=True)
            except BaseException as error:
                errors.append(error)
        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise BaseExceptionGroup("SSH host cleanup failed", errors)
