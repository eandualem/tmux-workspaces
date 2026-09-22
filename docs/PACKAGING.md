# Installation bundles and upgrades

Start with [source installation](../README.md#try-it) or [TPM setup](GUIDE.md#launch-from-tmux-with-tpm).
This page covers the local bundle installer, reproducible archives and safe
application replacement for contributors and packagers. A local Homebrew recipe
is included, but there is no published tap or GitHub release asset.

The core is a standard-library Python package with a `tmux-workspaces` console
command. A wheel does not include the source layout needed by the Ghostty and
TPM launchers, so the bundle installer retains that layout. No virtual environment
or third-party Python resources are needed for the bundle.

The local Homebrew recipe selects `python@3.14` and `tmux`; application minimums
remain Python 3.11 and tmux 3.3. The installer requires existing executables and
does not install dependencies. See [startup requirements](STARTUP.md) for the
checks applied when launching the installed application.

## Installed layout

`scripts/install_bundle.py` accepts a **new** `--prefix`, an absolute `--python`
and an absolute `--tmux` path. It refuses to overwrite even an empty existing
prefix. The interpreter/tool paths must contain no whitespace, and the tmux
executable must be named `tmux`; the application installation prefix may contain
spaces and quotes. A prefix inside the source Python package is rejected.

- `bin/tmux-workspaces` runs the copied standalone launcher.
- `bin/tmux-workspaces-ghostty` runs the optional dedicated Ghostty launcher;
  Ghostty is not a dependency of the core or formula.
- `libexec/tmux-workspaces/` contains the package, `run`, `ghostty`, the TPM
  compatibility script, `integrations/ghostty.conf`, `pyproject.toml`, README
  and the original MIT `LICENSE`. Paths are generated for this installation.
- `share/tmux-workspaces/tmux-workspaces.tmux` loads the same TPM integration.

Wrappers pin the interpreter and prepend the supplied tools' directories to PATH.
They disable initial Python bytecode writes; later helpers clear that environment
flag and may attempt caching. Python tolerates read-only package directories;
the installed PTY test exercises a read-only payload. Homebrew staging uses `--runtime-prefix` to
write final keg paths before moving owned files from the build directory; a
build-directory reference must never escape into the installed launchers.

The source-only `preview` helper is deliberately not installed: it currently
creates demo data beneath its checkout. Installed trials should instead use
`tmux-workspaces --data-dir /absolute/path/to/a/disposable-library --demo`.
Any future installed preview needs an external disposable-data location.

Ordinary library selection remains `--data-dir`, `TMUX_WORKSPACES_DATA_DIR`,
`$XDG_DATA_HOME/tmux-workspaces`, then `~/.local/share/tmux-workspaces`.
Never put a library in a keg, virtual environment, TPM clone or bundle prefix.
Configuration, sockets and saved layouts are not installation payloads.
These paths and shell-server identities are independent of the installation location.

## Install a local bundle

For development, keep using `./run` from a stable checkout. Do not move a checkout
while any viewer, generated Ghostty command or TPM binding references it.
For a local staged installation, from a trusted source checkout or extracted
bundle, choose an unused directory explicitly:

```sh
python3 scripts/install_bundle.py \
  --prefix "$HOME/.local/opt/tmux-workspaces-trial" \
  --python "$(command -v python3)" --tmux "$(command -v tmux)"
"$HOME/.local/opt/tmux-workspaces-trial/bin/tmux-workspaces" --help
```

Use the full command path initially. Adding a symlink to an existing PATH
directory is optional; check for an existing command before creating one.
For a source checkout, follow the [TPM setup](GUIDE.md#launch-from-tmux-with-tpm).
For a staged bundle, tmux can load its installed entry with an explicit path:

```tmux
run-shell '"$HOME/.local/opt/tmux-workspaces-trial/share/tmux-workspaces/tmux-workspaces.tmux"'
```

Use either the source plugin or the installed entry. They install the same launch
binding, default **Prefix W**, and read the existing `@tmux-workspaces-*` options.
No installer edits `~/.tmux.conf`, installs TPM, reloads a live server or changes
Ghostty settings. TPM loads executable `.tmux` entry files; this bundle keeps that
convention. See [TPM's plugin contract](https://github.com/tmux-plugins/tpm/blob/master/docs/how_to_create_plugin.md).

## Build a reproducible source archive

`scripts/package_source.py` archives a committed revision with `git archive`,
compresses it with an explicit gzip header (empty filename, timestamp zero, level 9
and portable OS byte), and records the full commit and archive SHA-256 in
`manifest.json`. It reads the formula template from that same commit;
working-tree changes and untracked memory/data are not exported.

The explicit header avoids the version-dependent OS byte in
[Python 3.11/3.12 gzip.compress](https://docs.python.org/3.11/library/gzip.html#gzip.compress).
Compressed body bytes can still differ between zlib versions. Reproduce an exact
checksum with the same Git/archive and compression toolchain, and always verify
the actual artifact digest. `check_package.py` checks that digest before extraction.

The destination must be new. Commit the intended changes before generating an
archive; otherwise they will not be included:

```sh
python3 scripts/package_source.py --revision HEAD --output .backbone/package-candidate
```

The output contains a source archive, manifest and concrete `tmux-workspaces.rb`
with a local `file:` URL, full revision in its prerelease version, MIT license and
SHA-256. Nothing is uploaded. A future release recipe must use an approved
immutable asset URL and its actual checksum, with the source commit recorded.
Never distribute a formula pointing at a moving branch or invent a checksum.
Homebrew's [formula cookbook](https://docs.brew.sh/Formula-Cookbook) describes
versioned sources, checksums, dependencies and installation/test blocks.

A generated `file:` recipe refers to that local archive. It is not a tap release
and cannot be used on another machine without transferring the artifact and
updating its location. Repository visibility does not publish a package.

## Upgrade and uninstall contract

The initial supported procedure is an **application-only replacement with
unchanged supported Python and tmux dependencies**:

1. Choose **Configure… → Detach** in every viewer using that installation. Keep tabs and panes;
   their ordinary shells and external sessions must remain running. Close the
   dedicated Ghostty instance after exiting its viewers, so a new surface cannot
   reuse an old generated command.
2. Install the candidate in a new immutable prefix. Keep the old files and any
   links until the candidate is verified. Never overwrite a running source tree,
   perform an in-place pip update in a live environment, or change library paths.
3. Repoint only explicitly owned launcher links. Reload the TPM entry from the
   new location; an existing binding embeds the previous installation path.
   Reopen the same library through the new command and verify arrangements,
   input routing and shell identities before retiring the old installation.
4. Roll back by exiting the new viewers and reopening through the retained old
   command with the same library. Across different versions, only claim rollback
   after checking schema compatibility; the relocation test below is one version.

Homebrew can clean old kegs, including dependency versions. Retaining an app keg
alone cannot preserve a Python executable and standard library it references.
Do not run cleanup/uninstall while those paths are still in use. See
[Homebrew's old-version guidance](https://docs.brew.sh/FAQ#how-do-i-keep-old-versions-of-a-formula-when-upgrading).
An ordinary `brew upgrade` may also upgrade dependencies, so the current evidence
does not establish it as a safe live-session upgrade procedure.

tmux commands resolve a client through PATH while persistent servers can remain
older processes. Arbitrary client/server upgrades are not proven compatible;
[upstream records a protocol mismatch](https://github.com/tmux/tmux/issues/4711).
Never repair such a mismatch by killing the user's persistent servers. Dependency
upgrade coverage or a retained compatible client is required before promising
unattended Homebrew upgrades with ongoing sessions. No `brew services` unit,
background updater or automatic cleanup is proposed here.

Uninstall removes only owned installation files and explicitly owned launcher
links, after all viewers using them exit. Remove the corresponding TPM configuration
entry yourself; that does not remove a binding already loaded in a running server.
Before manually unbinding, verify the configured key still points at this plugin.
Preserve libraries, sockets, ordinary shells, attached sessions and user settings.
The installer has no uninstall hook and never kills processes or deletes state.

## Verify an installation

```sh
make check
make smoke
make package-smoke
```

`package-smoke` tests the **committed HEAD**, extracting its generated archive
into a fresh directory and installing two copies. It runs outside the checkout
with a scratch home and restricted PATH, then exercises real terminal input,
mouse navigation/splits, attachment, foreground programs and the installed TPM
entry across first → second → first installations. All resources are disposable.
This is same-version relocation/reinstall coverage, not evidence for arbitrary
schema changes, dependency upgrades or live updates. Ghostty launch is inspected
with `--dry-run`; no native GUI is opened.

The formula calls the tested installer and includes noninteractive smoke checks.
Full Homebrew installation, audit, bottles, upgrade/cleanup behavior and supported
macOS/Linux packaging matrices still need isolated package-manager validation
before a tap release. The automated bundle tests do not use a normal Homebrew prefix.
Use source or TPM installation for normal use. Publishing a tap is a separate
release decision; generating or testing a local recipe does not publish it.
