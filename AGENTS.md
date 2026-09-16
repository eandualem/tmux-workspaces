# tmux-workspaces — project instructions

Applies to every agent and all work in this repository.

## Read first

Read README.md, docs/INITIAL_TASK.md and the relevant code and tests.
If `.backbone/memory/HANDOFF.md` and INDEX.md exist, read them for local context.
Shared memory, credentials, transcripts, runtime state and test artifacts stay
untracked. Development orchestration is optional; it is not a product dependency.

## Product contract

- Workspaces group terminals by purpose. User-named tabs own complete split layouts.
- New tabs have neutral random names. New tabs and splits open empty, offering a
  normal interactive shell or an existing session in the pane itself.
- Existing tmux sessions attach optionally. Attachment never renames a tab.
- Persist grouping, names, split arrangements and offline attachment references.
- Support keyboard and mouse operation. Describe implemented capabilities accurately.
- Preserve shells/cwd/history through navigation, resize and viewer close.
- Closing a pane/tab may end its own shells; never stop an attached external session.
- Keep the core useful without Backbone, Ghostty, a browser or notifications.
- Backbone remains an explicitly enabled, read-only adapter with no default imports.

## Isolation and verification

Work in this checkout and scratch resources you create. Existing user libraries,
sessions and other checkouts are protected. Never migrate or clean them up as a
side effect of tests, packaging or a refactor. Keep the imported MIT notice.
Use unique private tmux sockets and disposable ordinary shells for tests. Never
use an unqualified kill-server, move external panes or message real agents in tests.
Do not change another agent's lifecycle or configuration. Do not weaken a sandbox.
Keep working behavior; avoid a rewrite. Add dependencies only with a reason.
Run `make check` before commits and `make smoke` after terminal/UI changes.
Record actual evidence separately from expected behavior and untested platforms.

## Release boundaries

Topic branches open pull requests into `develop`; `main` is promoted only when the
repository owner decides. Public repository visibility and package-registry
publication are separate release decisions reserved for the repository owner.
