# Terminal integration tests

Tests use disposable ordinary shells, fresh libraries and explicitly qualified
private tmux sockets. They never use an existing library, source session or real
agent. Run commands below from the checkout root. Python 3.11+, tmux 3.3+, a UTF-8
locale and `xterm-256color`/`tmux-256color` terminfo are required. Runtime version
floors are requirements, not a claim that every version combination was tested.

## Local and focused commands

```sh
make check
make smoke
python3 -m tests.integration.smoke_preflight
python3 -m tests.integration.smoke_shortcuts
python3 -m tests.integration.smoke_menus
python3 -m tests.integration.smoke_windows
python3 -m unittest tests.unit.test_integration_support tests.unit.test_ssh_support -v
```

`make check` runs Ruff, shell syntax and unit tests. Supply `RUFF="python3 -m ruff"`
if Ruff is already installed; otherwise the default uses uv. `make smoke` runs the
PTY scenarios, including startup-failure isolation and SSH on Linux. See
[startup requirements and failure tests](STARTUP.md). The old experiment smoke launchers remain
compatibility entry points. Each scenario can also run as its module under
`tests.integration`. Failures include the predicate that timed out; waits use a
monotonic deadline rather than assuming a fixed startup time.

On Debian/Ubuntu, install terminal test prerequisites with:

```sh
sudo apt-get install -y tmux locales ncurses-term openssh-client openssh-server
sudo locale-gen en_US.UTF-8
sudo install -d -m 0755 /run/sshd
```

The fixture starts its own daemon with its own configuration. It does not connect
to or configure the system SSH service. On macOS, ordinary PTY suites need tmux
(for example, `brew install tmux`). SSH is explicitly skipped by default on
non-Linux hosts: macOS daemon policy can reject its pre-authentication sandbox
inside a restricted development environment. To require SSH on a supported local
host, run `make smoke-ssh`. That command fails on missing prerequisites or daemon
startup failure; it never silently turns a failed scenario into a skip. Do not
change system security settings to make a test pass; use the container recipe.

## Isolated Linux and SSH

The test image contains tools only. Building with the Dockerfile on stdin sends
no checkout files as build context. Docker needs network access to obtain its base
image and packages; the actual test container has no external network and reads
the checkout through a read-only mount. It runs as an unprivileged fixture user.

```sh
docker build -t tmux-workspaces-tests - < tests/integration/Dockerfile
docker run --rm --init --network none \
  --mount "type=bind,src=$PWD,dst=/workspace,readonly" \
  tmux-workspaces-tests
```

The default image is Python 3.14 on Debian. To select another official Python
image, add `--build-arg PYTHON_IMAGE=python:3.12-slim` to the build command. To run
only SSH, append `make smoke-ssh` to `docker run`. No port publishing, privileged
container, SSH-agent forwarding or user-home mount is needed. Fixtures create all
writable data in container scratch space. Ruff caching is disabled in the image
so the full check command also works with the read-only checkout.

The SSH scenario generates temporary Ed25519 host/client keys, pins the host key,
and starts a loopback-only daemon on an ephemeral port. Authentication is public
key only; forwarding, user rc files and inherited client configuration are disabled.
A forced command launches this checkout with a fresh HOME and explicit library
and source socket. No existing keys, credentials or SSH service are used.

The encrypted connection carries mouse reports, prefix/direct shortcuts, returned
terminal output and window-size changes. The scenario drops the SSH client,
reconnects, verifies saved workspaces and four-pane arrangements, and resumes the
same foreground process. It checks cwd and shell PIDs, parked-shell preservation,
offline attachment recovery, and external-session survival across disconnect and
normal viewer exit. It verifies real loopback SSH transport; it does not simulate
WAN latency, prove a native terminal's painting, or test SSH-agent credentials.

## Fixture ownership

Use `FixtureResources` from `tests.integration.support` as the outer context for a
new scenario. `library()` creates a fresh directory; `server()` reserves a short
private socket; `client()` registers a real PTY process with isolated HOME.
`own_client()` and `own_cleanup()` register custom clients and other resources.
Never adopt an existing library or server as a fixture. Set scratch HOME explicitly
when creating an external source shell through a registered `Tmux` object.

Teardown attempts every registered cleanup even after assertion failures,
interruptions or another cleanup failure. Clients are terminated and reaped before
servers and scratch files are removed. Server cleanup uses the fresh owner's socket
namespace, not socket paths read from runtime manifests. A corrupt manifest cannot
redirect teardown to an unrelated server. Cleanup failures are reported; an original
scenario failure keeps its identity and receives a cleanup note. Unit tests inject
PTY setup failures, timeouts, interruptions and hostile manifest paths.

## Coverage and limits

The startup-preflight follow-up passed 187 application and 12 benchmark tests on
both platforms below, plus eleven Linux and ten macOS PTY scenarios (SSH skipped
on macOS). Its failure fixtures establish empty-library/no-server behavior for
unsupported environments. Full evidence and the final preview exit-status check
are recorded in [ACCEPTANCE.md](ACCEPTANCE.md).

The combined fixture/keymap branch passed `make check` with 164 application tests
and 12 benchmark tests on macOS and Linux. All ten PTY scenarios passed on Linux;
nine passed on macOS with an explicit SSH skip. macOS used Python 3.14.7 and tmux
3.7c. The Debian 13 aarch64 container used Python 3.12.14,
tmux 3.5a and OpenSSH 10.0p2. Both use `LANG=en_US.UTF-8` and
`TERM=xterm-256color`; resize exercises narrow and four-pane dimensions.

The configured hosted matrix is Ubuntu/Python 3.11 and 3.14 plus macOS/Python 3.14.
Linux jobs require the SSH scenario; macOS jobs explicitly skip it. The previous
merged terminal suites passed that matrix, but this change's hosted runs remain
pending account availability. Record final-head CI results before merging.
Native macOS SSH, WSL, the exact tmux 3.3 floor, real network latency and native
Ghostty GUI rendering remain unverified by these fixtures.
