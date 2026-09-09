# Contributing

Issues and pull requests are welcome.

## Running the checks

```sh
make check    # lint, formatting, shell syntax, unit tests
make smoke    # the real-terminal suites
```

`make check` uses [uv](https://docs.astral.sh/uv/) to fetch Ruff. If you already
have it, pass yours instead: `make check RUFF="python3 -m ruff"`.

`make smoke` drives real PTYs. It creates its own tmux servers on private
sockets with disposable shells, and never touches your own sessions or your
saved workspaces. On Linux it also runs an SSH scenario; see
[docs/TESTING.md](docs/TESTING.md) for prerequisites and for the container
recipe that runs the Linux suites in isolation.

## What a change needs

- `make check` passing, and `make smoke` too if you touched anything the
  terminal draws or any input path.
- A test for the behaviour you changed. The unit suites cover everything that
  does not need a terminal; the suites under `tests/integration/` cover what
  does.
- Honest documentation. If a limit exists, say so — several docs here record
  what is *not* verified, and that is deliberate.

## Where things live

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes what each module owns.
The short version: `tmux_workspaces/` is the runtime, `tests/unit/` needs no
terminal, `tests/integration/` drives real PTYs, and `docs/` is the reference
material the README links to.

## Branches

`develop` is the integration branch — open pull requests against it. `main`
tracks released state and is updated from `develop` at intervals.
