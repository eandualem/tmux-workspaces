# Ordinary shell environment

New tabs and splits start normal interactive login shells. The launcher forwards
an explicit set of environment variables instead of inheriting every variable
from the process that opened it. This keeps agent-launcher identity and unrelated
API keys out of ordinary terminals while retaining normal Unix session paths.
The user's shell startup files still run and may set their own environment.

## Supported variables

Terminal and basic shell context is retained: `PATH`, `HOME`, `USER`, `LOGNAME`,
`SHELL`, `TERM`, `LANG`, `TMPDIR`, `COLORTERM`, `TERMINFO`, `TERMINFO_DIRS`, and
locale variables beginning with `LC_`. tmux supplies the pane's terminal settings;
the ordinary shell explicitly removes the private `TMUX` and `TMUX_PANE` identity.

In addition, each newly created ordinary shell receives these values from the
viewer that creates it, when present:

- `SSH_AUTH_SOCK`: the local or SSH-forwarded agent socket path.
- `XDG_CONFIG_HOME` and `XDG_CONFIG_DIRS`.
- `XDG_DATA_HOME` and `XDG_DATA_DIRS`.
- `XDG_CACHE_HOME`, `XDG_STATE_HOME`, and `XDG_RUNTIME_DIR`.

Paths and directory lists are passed as supplied, including spaces. No directories
or agent sockets are created or migrated by this policy. `SSH_AGENT_PID`,
`SSH_CONNECTION`, desktop-session variables, arbitrary `XDG_*` names and unrelated
launcher variables are not forwarded. API keys are not inferred or copied from
Backbone or other agent tools. Programs needing additional environment can obtain
it from the user's normal shell configuration or explicit exports.

The viewer's new private server carries the SSH/XDG context to its controller.
Control commands and external-session discovery retain the narrower environment;
they do not receive these additional paths. Existing-session attachment uses
`tmux attach-session -E`, preserving that session's own environment. Attaching an
external session does not replace its shell context with the viewer's context.

## Reconnecting over SSH

The library's ordinary shell server can survive the SSH connection that started
it. A forwarded agent socket may disappear when that connection closes.

After reconnecting, launch the viewer again with the same library. New tabs and
splits then receive the current SSH/XDG values from that viewer. Missing values
are explicitly unset for new shells, so the persistent server cannot accidentally
supply an old agent socket. Two viewers can create shells with different contexts
without changing each other's existing shells.

An already-running shell retains its environment, PID, working directory and
foreground processes. Reopening a viewer cannot change another process's exported
environment. If that shell should use the new forwarded socket, first obtain the
current path from the fresh SSH login terminal:

```sh
printf '%s\n' "$SSH_AUTH_SOCK"
```

Then explicitly set that path in the existing ordinary shell:

```sh
export SSH_AUTH_SOCK='/the/current/forwarded/socket'
```

Use `unset SSH_AUTH_SOCK` there if the new connection has no agent. Future commands
started by that shell receive the changed value; already-running child processes
keep their prior environment. Other XDG settings can be changed in the same way.
The viewer does not rewrite shell profiles, inject exports into live terminals,
redirect sockets, or reconnect an SSH agent on the user's behalf.

## Verification

`python3 -m tests.integration.smoke_environment` uses a private library, ordinary
shells, a temporary HOME, two disposable mock Unix sockets and synthetic secrets.
Ordinary Python commands connect to the mock socket and read a file through
`XDG_CONFIG_HOME`. The test checks launch, reopen with changed/expired context,
new tabs, simultaneous viewers with absent context, unchanged original PID/cwd,
and external-session environment preservation. It does not use a real SSH
connection, agent key or user configuration. The suite is part of `make smoke`.
