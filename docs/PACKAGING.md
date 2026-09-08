# Packaging decision and local prototype

This is the design/prototype for [issue #12](https://github.com/eandualem/tmux-workspaces/issues/12),
dated 2026-09-08. No tap, release asset, bottle or Python package has been published.
Repository visibility and package publication remain separate owner decisions.
The [naming decision](NAMING.md) plans Muxstead; this prototype retains all
`tmux-workspaces` identifiers until the compatibility rebrand is implemented.

## Decision

Keep source installation and TPM as supported entry points. Prototype a personal
Homebrew **tap**, without a homebrew-core submission. A tap is a separate formula
repository; creating this local recipe does not create that repository.
See [Homebrew's tap documentation](https://docs.brew.sh/Taps).

The issue's initial evidence has changed: `pyproject.toml` now defines a setuptools
package, Python 3.11+ and an installed `tmux-workspaces` console command. The core
can use `python -m tmux_workspaces` when installed. However, the TPM and Ghostty
modules still reference the source-tree `run`, compatibility script and profile.
A plain wheel is not yet the full installation contract for those integrations.

For the first tap, install a **source bundle with its existing runtime layout**.
The runtime is standard-library-only, so this requires no virtual environment or
third-party Python resources. Preserve the internal package and distribution
identity; do not introduce a second distribution owning the same Python files.
Revisit wheel resource lookup separately if pip/uv distribution becomes a priority.

The prototype recipe declares `python@3.14` and `tmux`; the application floors
remain Python 3.11 and tmux 3.3. Python 3.14 is the formula's selected interpreter,
not a new minimum. Homebrew maintains those dependencies:
[Python formula](https://formulae.brew.sh/formula/python@3.14),
[tmux formula](https://formulae.brew.sh/formula/tmux).
The installer requires supplied executables; it does not install dependencies.
Clear application startup/version checks are pending in
[PR #26](https://github.com/eandualem/tmux-workspaces/pull/26).

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
The planned rebrand must retain these paths and shell-server identities.

## Source and TPM installation

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
The current source-linked TPM installation remains documented in the README.
For a staged bundle, tmux can load its installed entry with an explicit path:

```tmux
run-shell '"$HOME/.local/opt/tmux-workspaces-trial/share/tmux-workspaces/tmux-workspaces.tmux"'
```

Use either the source plugin or the installed entry. They install the same launch
binding, default **Prefix W**, and read the existing `@tmux-workspaces-*` options.
No installer edits `~/.tmux.conf`, installs TPM, reloads a live server or changes
Ghostty settings. TPM loads executable `.tmux` entry files; this bundle keeps that
convention. See [TPM's plugin contract](https://github.com/tmux-plugins/tpm/blob/master/docs/how_to_create_plugin.md).

## Immutable artifacts and private access

`scripts/package_source.py` archives a committed revision with `git archive`,
compresses it with a fixed gzip timestamp, and records the full commit and archive
SHA-256 in `manifest.json`. It reads the formula template from that same commit;
working-tree changes and untracked memory/data are not exported. The destination
must be new. Generate a local candidate only after committing the prototype:

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

While the repository is private, a developer first obtains source with their
own authorized Git/SSH or GitHub CLI session, then builds locally. Neither the
formula nor archive contains tokens, authentication headers or SSH keys.
A `file:` recipe is specific to the developer's artifacts and is not a tap release.
Anonymous installation from this private repository is not promised. A private
tap alone would not grant access to its private source assets.

## Upgrade and uninstall contract

The initial supported procedure is an **application-only replacement with
unchanged supported Python and tmux dependencies**:

1. Choose **Exit** in every viewer using that installation. Keep tabs and panes;
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

## Local verification and remaining release checks

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
before a tap release. No normal Homebrew prefix is used by this issue's tests.
Source/TPM remain the working installation paths until those checks and the
owner's separate publication decision are complete.
