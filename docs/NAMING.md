# Naming decision: Muxstead

Decision recorded 2026-09-08 for [issue #11](https://github.com/eandualem/tmux-workspaces/issues/11).

Select **Muxstead** for the planned rebrand: **Muxstead — a workspace layer for
tmux.** Pronounce it “mucks-sted”; the prospective command is `muxstead`.
This is a reviewable project decision, not a claim that the owner selected the
name. The operational name remains `tmux-workspaces` until a separate
compatibility change lands. This document changes no command, repository name,
package, settings or saved library.

“Mux” connects the name to tmux; “stead” suggests a place to return to. Together
they suit arrangements organized by purpose. Retain **Pull, not push** as a named
principle: check in when you choose, without notifications or interruptions.
The arrangement persists; tmux owns the processes. The name must not imply an
agent manager, process recovery after reboot, or a required integration.

## Shortlist and discovery

The existing name overlaps with several repositories, including
[niflostancu/tmux-workspaces](https://github.com/niflostancu/tmux-workspaces),
[jakescript/tmux-workspaces](https://github.com/jakescript/tmux-workspaces) and
[themastersheep/tmux-workspaces](https://github.com/themastersheep/tmux-workspaces).
[twm](https://github.com/vinnymeller/twm) already means Tmux Workspace Manager.
A distinct product name with the explicit category phrase is preferable to
another variation on those names.

Three candidates received the same bounded screen on 2026-09-08:

- **Muxstead** — eight ASCII letters, “mucks-sted.” The strongest fit for a
  stable arrangement of purposes, while retaining the tmux connection. GitHub
  repository search for `muxstead in:name` returned zero results; the exact
  account lookup returned 404. Exact-name web searches did not identify a clear
  software product. Tradeoffs: the consonant cluster benefits from a pronunciation
  cue, and the name needs the category phrase to explain the product.
- **Panehold** — eight ASCII letters, “pane-hold.” The same GitHub searches and
  account lookup returned zero results and 404, respectively; web searches did
  not identify a clear software product. It is easy to spell but emphasizes panes
  rather than purposes. “Hold” can suggest process retention, and the “pain hold”
  homophone is distracting. Retain as the second choice.
- **Termstead** — nine ASCII letters, “term-sted.” A good place-to-return-to
  association, but an existing [Termstead organization](https://github.com/termstead)
  owns [reduco](https://github.com/termstead/reduco). GitHub search returned that
  owner-name match, not a repository literally named Termstead. Its product domain
  is unknown; the identity overlap is enough to reject it for this decision.

Fit and pronunciation assessments are editorial judgment, not user research.
None of these names uses “sidebar” or “lazy” as its product/category identity.
Two other names were rejected before this shortlist: [Muxloom](https://muxloom.com/)
already presents an AI terminal product, and
[Paneweave](https://github.com/querielo/paneweave) names a split-layout package.

### What the checks establish

Authenticated GitHub repository searches used `<name> in:name` and reported
`incomplete_results=false`. Exact account lookups used `/users/<name>`;
a follow-up on the same date also checked `/orgs/<name>` for all three candidates.
Both endpoints returned 200 with `type: Organization` for Termstead, and 404
for Panehold and Muxstead. The observed `/users/termstead` response therefore
already included that organization; the separate organization probes confirm it.
The reproducible primary endpoints for the selected candidate are
[repository search](https://api.github.com/search/repositories?q=muxstead+in:name),
[account lookup](https://api.github.com/users/muxstead) and
[organization lookup](https://api.github.com/orgs/muxstead).
General web queries used each quoted name alone, with software/terminal/CLI
terms, and restricted to GitHub. They missed the Termstead organization, which
illustrates why an empty web search is insufficient evidence of availability.

For each of the three lowercase candidate names, the exact package endpoints
at PyPI, npm and crates.io and the Homebrew formula/cask endpoints returned
HTTP 404. For Muxstead these are
[PyPI](https://pypi.org/pypi/muxstead/json),
[npm](https://registry.npmjs.org/muxstead),
[crates.io](https://crates.io/api/v1/crates/muxstead),
[Homebrew formula](https://formulae.brew.sh/api/formula/muxstead.json) and
[Homebrew cask](https://formulae.brew.sh/api/cask/muxstead.json).
The development process's PATH contained no executable named `muxstead`,
`panehold` or `termstead`.

These dated observations support choosing a candidate; they do not establish
uniqueness or availability. Package names can be reserved despite a 404, and
executable names can differ from their packages. The screen does not cover every
distro, personal tap, private repository, language, domain or trademark. No name
was reserved or purchased. Repeat the checks before activating the rebrand or
requesting publication.

Remote use remains central to the product description: the workspace layer runs
on the tmux host and needs no local GUI application. Do not claim that competing
products cannot use SSH; [cmux documents SSH workspaces](https://cmux.com/docs/ssh).
Use the on-host interface, purpose arrangements and pull attention to explain
the distinction, without unsupported exclusivity claims.

## Compatibility plan

The first rebrand should add the new identity without migrating state. Apply the
following in a separate implementation PR, after the pending launcher/keymap work
has landed. The naming document itself is independent of that work.

1. **Command and module aliases.** Add `muxstead` as another entry point to the
   same application. Retain `tmux-workspaces`, `./run`, `./ghostty`, `./preview`,
   `python -m tmux_workspaces` and existing compatibility wrappers. Keep the
   internal Python package and distribution name initially. Running helpers can
   contain absolute checkout paths: do not move or delete an existing checkout
   or TPM clone as part of renaming. Do not claim short aliases such as `twm`.
2. **One library and one shell owner.** Keep the current precedence:
   `--data-dir`, then `TMUX_WORKSPACES_DATA_DIR`, then
   `$XDG_DATA_HOME/tmux-workspaces`, then `~/.local/share/tmux-workspaces`.
   Keep those defaults even for new installations in the first rebrand. Do not
   discover, copy, merge or rename a library automatically. Preserve the canonical
   library path, private socket namespace and hashing, stable leaf/session IDs,
   database schema and saved attachment references. Changing a socket prefix can
   start a different server and strand running shells even if the database is
   unchanged. Existing explicit library paths must keep working through either
   command.
3. **Settings and generated launchers.** Retain existing environment and tmux
   option names; a branding change does not require new settings aliases. Once
   [PR #24](https://github.com/eandualem/tmux-workspaces/pull/24) lands, this also
   covers `TMUX_WORKSPACES_KEYMAP`, the existing XDG keymap path and per-viewer
   snapshots. Preserve generated Ghostty commands and installed helper paths.
   Do not edit the user's ordinary terminal configuration.
4. **TPM compatibility.** Retain `tmux-workspaces.tmux` and all
   `@tmux-workspaces-*` options. A future `muxstead.tmux` entry point should forward
   to the same plugin loader; loading both must not install duplicate behavior.
   Document either entry point without rewriting the user's tmux configuration
   or relocating an existing plugin checkout.
5. **Repository and packaging.** A later private repository rename may update
   remote URLs, documentation and issue routing, while keeping local checkout
   paths. Verify redirects after the rename instead of assuming them. Keep the
   current distribution identity until a separately reviewed packaging plan
   handles upgrade ownership; do not install two distributions that own the
   same Python files. Public visibility and publishing to any package registry
   remain separate decisions reserved for the owner, with no assumed date.

There is no data migration in this plan. Any later request for branded storage
paths needs its own explicit migration design, ownership checks and rollback.
The first rebrand can roll back its display name and command alias while opening
the same library and shell server through the old command.

### Acceptance for the implementation

Use disposable libraries and private tmux sockets to verify both command names
open the same arrangement and retain shell PIDs, cwd and foreground programs.
Create the library with the old entry point, reopen through the new one, then
return to the old one. Cover custom paths, XDG/environment precedence, concurrent
viewers, offline associations and external-session survival. Check old helper
paths, normal and Ghostty launch, both TPM entry points loaded together, and
existing keymap/settings names. Run `make check` and `make smoke` for that change.

The present PR is documentation only. Name activation and the implementation
checks above are planned work, not claimed test results.
