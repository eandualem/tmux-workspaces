# Project identity and compatibility

The current project, command and distribution name is **tmux-workspaces**; the
Python package is `tmux_workspaces`. **Muxstead — a workspace layer for tmux**
is the proposed future name. There is no `muxstead` command in this version.
The product organizes terminals by purpose; tmux owns the running processes.
Neither name implies process recovery after reboot or a required agent manager.

## Compatibility boundaries

A name change must preserve existing installations and running terminals:

- Keep existing commands, module entry points and executable compatibility
  wrappers. Running viewers and TPM bindings can contain absolute checkout paths;
  moving a checkout can break them even when the saved library is unchanged.
- Keep one library and shell owner. Selection is `--data-dir`, then
  `TMUX_WORKSPACES_DATA_DIR`, then `$XDG_DATA_HOME/tmux-workspaces`, then
  `~/.local/share/tmux-workspaces`. Preserve socket identities, stable leaf/session
  IDs, the database schema and saved attachment references. Changing a socket
  prefix can strand running shells on a different server.
- Retain environment variable names, XDG configuration paths, keymap snapshots,
  generated Ghostty commands, `tmux-workspaces.tmux` and `@tmux-workspaces-*`
  options. Additional entry points must not load the plugin twice or rewrite
  a user's terminal configuration.
- Keep a single Python distribution responsible for the package files. Verify
  repository redirects if URLs change. Registry publication is separate from
  repository naming or visibility.

These constraints require no data migration. A future storage-path change needs
an explicit migration and rollback design. Before activating another name, repeat
name-availability checks; an empty search or registry response does not establish
trademark clearance or reserve a name.

## Verify a compatibility change

With disposable libraries and private tmux sockets, create an arrangement through
the old entry point, reopen it through the new one, then return to the old one.
Check shell process IDs, cwd, foreground programs, custom paths, XDG/environment
precedence, concurrent viewers, offline attachments and external-session survival.
Check retained helper paths, normal and Ghostty launch, TPM loading and settings.
Run `make check` and `make smoke`. This describes checks needed for a future
change, not evidence that a rebrand has been implemented.
