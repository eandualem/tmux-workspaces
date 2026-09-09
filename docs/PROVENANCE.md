# Provenance

The original terminal workspace prototype was developed in `eandualem/agent-backbone`,
on `experiment/workspace-viewer`, based on development commit
`ba881421fc455f6dd52f7385923c319e1c22da54`. The prototype itself was uncommitted at
extraction on 2026-09-07; that upstream commit does not contain its implementation.
The initial snapshot here comprised the viewer, usage documentation and tests
under `experiments/workspace_viewer/`.

The imported MIT notice is retained in [LICENSE](../LICENSE), alongside a copyright
line for this project's own contributors under the same MIT terms. Development since
extraction adds independent launch, generic attachment, an opt-in read-only adapter,
TPM and Ghostty entry points, compact navigation, and inline tab/workspace names.
No upstream service code, existing layouts, agent data, credentials or transcripts
are included. The runtime does not import or depend on the upstream checkout.

The original snapshot passed 17 unit tests, lint/format checks, real mouse/PTY
four-pane layouts, persistent foreground programs across exit/reopen, concurrent
windows and an xterm-ghostty terminfo check. Current results and platform limits
are in [ACCEPTANCE.md](ACCEPTANCE.md).
