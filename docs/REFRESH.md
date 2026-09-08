# Refresh the viewer safely

Viewer refresh reloads viewer code and settings while keeping the work beneath
the viewer running. It is not an updater or dependency installer.

With the shipped keymap, press **Ctrl-g**, release it, then **f** to open the
refresh confirmation. The shortcut controls also expose **Refresh viewer** by
mouse. Save or cancel an open name or color edit first. Cancel the confirmation to keep
the current viewer.

## Refresh or reopen

Shared workspace names, tabs, arrangements and attachment availability already
refresh during normal use. They do not need a viewer restart.

A refresh launches a fresh interpreter for the current viewer and reloads its
application modules, selected theme and keymap files, including prefix bindings and help
labels. A parent process remains available to report replacement failures.
Existing ordinary shells, parked shells and external sessions remain on their
own servers. Other viewer windows keep their own navigation and launch context.

Changes to the Python interpreter, tmux, installation location, launch options or
the terminal itself require reopening with the intended launcher. The small parent
that supervises replacements remains at its initial version until reopening.
Refresh does not install an update or restart a shell server. Changing the hosting
tmux's TPM launch binding still requires reloading that plugin in the host.

The dedicated Ghostty launcher fixes its native shortcut profile when the terminal
instance opens. A viewer refresh cannot update those terminal bindings safely.
Use the manual reopening path and its exact command to open a new dedicated
instance. Opening another surface of the old instance retains its old profile.

## Preservation and failure behavior

Refresh saves the current layout before the current display closes. Finish or
cancel a pending name or color edit first. A save conflict or invalid edited keymap
leaves the viewer open
with an error so the problem can be resolved. Cancellation leaves the existing
viewer running.

Each replacement gets fresh display and action sockets and a fresh runtime
identity. Repeated requests for one refresh create at most one replacement. Once
a refresh is confirmed, further queued actions for that old viewer, including
Exit, are ignored. Exit remains available in the replacement. The
canonical library, attachment source, optional adapter selection, working directory
and theme and keymap selections belong to the launch context, rather than a temporary viewer.
A refreshed instance does not reuse a stale keymap snapshot.

If the replacement fails, the launcher prints the reason and an exact quoted
reopen command. In an interactive terminal it then becomes an ordinary recovery
shell, keeping the instructions visible. Exiting that shell ends recovery, with
the shell's exit status; a noninteractive failure returns a failing status.
Reopening never closes a tab or pane, stops an external session, downloads a
package or rewrites user configuration.

If a viewer ignores a stop signal, the launcher gives it ten seconds to finish
cleanup before ending it. The message explains that cleanup may be incomplete;
closing the terminal detaches the remaining display client. The library's shells
and attached external sessions remain separate.

The running processes survive only while their original tmux servers remain alive.
A host reboot or independently stopped shell server cannot preserve process state.

The disposable `--demo` mode recreates its sample attachment sessions with each
display. Their processes reset; use ordinary shells and external sessions for
work that must survive refresh.
