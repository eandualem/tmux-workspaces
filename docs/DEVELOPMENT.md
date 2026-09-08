# Development and review

Keep each change focused on a concrete problem with observable acceptance criteria.
The runtime uses the Python standard library; add dependencies only when their
benefit justifies the installation and maintenance cost.

## Local checks

```sh
make check
make smoke
```

`make check` runs Ruff, formatting checks, plugin shell syntax and unit tests.
`make smoke` exercises real tmux servers through disposable PTYs. Run it after
changes to terminal behavior, lifecycle, input or runtime structure. Tests must
create their own sockets and libraries; existing sessions are never fixtures.

Ruff can also be supplied explicitly without uv:

```sh
python3 -m pip install ruff==0.16.6
make check RUFF="python3 -m ruff"
```

## Pull requests

Use a focused branch and reference the issue in the PR. Describe the resulting
behavior, relevant design choices, checks and remaining limitations. An independent
reviewer should inspect correctness, lifecycle ownership, compatibility and the
test evidence. Record the review findings and their resolution on the PR; an
agent review must be identified as such.

Address actionable review feedback before merging. Wait for GitHub checks to pass
on the current commit, squash-merge the PR, then verify the implementation issue
closed. If a PR implements only part of an issue, describe the remaining work and
keep that issue open. Rebase dependent branches after each merge. Remove merged
branches when no active worktree or running helper still depends on them.

GitHub CI runs checks and all terminal integration suites on Linux with Python
3.11 and 3.14, and macOS with Python 3.14. Jobs use read-only repository permissions
and no user credentials. Passing PTY tests does not establish native GUI rendering
or end-to-end SSH coverage. Performance budgets belong in opt-in benchmarks,
rather than timing assertions in ordinary unit tests.

Public visibility and package-registry publication remain separate owner decisions.

## tmux option compatibility

The TPM launcher probes whether the server adds a backslash before dollar-variable
spellings when printing option values, a behavior present in tmux 3.4. It removes
only that added escape and never evaluates shell syntax or unescapes other text.
The integration test verifies literal paths, quotes, dollars and backslashes.
Do not generalize this to decoding control characters: tmux's textual output can
make a backspace indistinguishable from the literal characters `\b`.
