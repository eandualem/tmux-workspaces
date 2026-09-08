# Performance measurement

Switching performance is a public-release gate. Before optimization, the target
for **each** one- and four-pane tab/workspace navigation path is **p50 ≤ 150 ms
and p95 ≤ 250 ms**, for both clicks and direct shortcuts. These are engineering
acceptance budgets, not a claim about the current version. Meet them for both
shell-client readiness and the routed-input acknowledgement in repeated untraced
runs. Timing is deliberately not asserted by ordinary unit tests.

## Run on disposable terminals

From the checkout, with Python and tmux available:

```sh
mkdir -p .backbone/benchmarks
python3 scripts/benchmark.py > .backbone/benchmarks/baseline.json
python3 scripts/benchmark.py --viewers 2 > .backbone/benchmarks/two-viewers.json
```

The default runs one-, four- and eight-pane layouts, three 30-second idle samples
per layout, and 20 events in each navigation group. Expect several minutes. All
servers, layouts, source sessions and shells are disposable, with unique sockets
and temporary directories. The shell HOME is temporary, so personal shell startup
files are not loaded. The benchmark never uses the default tmux socket or an
existing workspace library. Fixture cleanup closes only its own servers. It attempts every owned client,
server and lock even if an earlier cleanup fails, then reports aggregated errors;
PTY descriptors close even if process termination fails.

For a quick harness check, not an acceptance measurement:

```sh
python3 scripts/benchmark.py --panes 1 4 --samples 2 \
  --idle-samples 1 --idle-seconds 1 --warmup .2
```

Use `--help` for durations, sample counts and polling controls. Navigation sample
counts are even so both destinations are exercised equally. Each workspace has two
tabs with the chosen pane count; all four tabs are warmed before sampling. Only
one tab per viewer is displayed; parked shells remain in the CPU scope. With two
viewers, the second remains on its original tab while the first navigates.
Resize and external-session offline/reconnect probes also run on each fixture;
they are diagnostic single samples, not p95 estimates. No agent model is involved.

## What the JSON measures

- **Shell-client readiness:** from writing the complete input event into the PTY
  until the destination layout has the expected leaf identities and all its
  ordinary shell sessions have clients attached from those exact display pane
  TTYs. Another viewer attached to the same shell cannot satisfy this check.
- **Routed acknowledgement:** after that check, write a unique `printf` probe
  through the same terminal input path, then observe its result in the selected
  underlying shell. Echo of the typed command cannot satisfy the marker check.
  After the timed interval and settling, every other fixture shell is checked
  for the same marker. Duplicate delivery fails the run even if the intended
  shell received it too. This verification is outside the reported latency.
  The timing includes probe execution and observer cost; it does not prove that typing
  immediately alongside a click is safe. The manual check below covers that case.
- **First PTY output:** the first output bytes drained after the event, plus the
  byte count. These may be partial or unrelated terminal updates. This is **not**
  native Ghostty paint, frame completion or a measure of the selection highlight.
- **Observer cost:** synchronous tmux query count and wall time accompany each
  event. The configured pump interval applies per viewer, and query time adds to
  resolution. Do not subtract observer time to manufacture a faster result.
- **Idle CPU:** the complete owned viewer, tmux-server and ordinary-shell trees,
  including waited-for children; 100% means one CPU core. The observer has a
  separate self-plus-waited-child CPU total, including its process-table queries.
  CPU samples with changed process sets or regressing counters are invalidated.

On macOS, CPU uses `ps -S` cumulative self and waited-child time. On Linux it uses
`/proc/PID/stat` user/system and waited-child ticks. Sum each owned live process
once, including overlapping roots; do not add recursive ancestor totals. Short
helpers that exit and are reaped during idle are charged to their parents.
Both methods have clock granularity and enumeration races; stable endpoint PIDs
do not prove that all transient processes were observed. Processes that escape
the owned tree are not accounted for. macOS sandboxes may deny process-table
access; use a normal local terminal rather than disabling a sandbox globally.
Linux accounting is implemented but needs a measured Linux baseline.

The raw JSON records the Git revision, tracked dirty state, changed tracked paths
and a SHA-256 fingerprint of the tracked diff. Dirty-checkout and traced results
are explicitly marked `acceptance_eligible: false`; a HEAD hash alone is not
provenance for modified runtime code. Eligibility is not a passing result or a
substitute for repeated runs and human acceptance. It also records
OS release, architecture, logical CPU count, versions,
dimensions, workload, warmup, configuration, every sample, median and nearest-rank
p95. It does not record the host name, operator paths, credentials or shell history.
For acceptance, retain at least three independent runs under comparable machine
load and power settings, and record those settings alongside the JSON. Avoid
simultaneous builds. Twenty events provide only a coarse tail estimate; increase
`--samples` when assessing regressions or inconsistent results.

## Command and interpreter diagnostics

```sh
python3 scripts/benchmark.py --panes 1 4 --samples 2 \
  --idle-samples 1 --idle-seconds 1 --trace-commands \
  > .backbone/benchmarks/commands.json
```

Tracing adds a temporary shell wrapper only to the fixture's PATH. It counts
application tmux invocations separately from the observer's direct tmux queries.
This wrapper adds process and file-I/O cost: compare traced runs only with traced
runs, and use **untraced** runs for the latency gate. Batched tmux subcommands
count as one invocation. The separate `helper_import_baseline` runs five fresh
interpreters importing the viewer module and exiting, without actions or tmux work. It characterizes
interpreter/module startup, not the cost of an actual `_action` request or
individual rendering stages. The main viewer stays alive; keyboard
`_action` helpers and attachment `_leaf` helpers may start during interaction.
Do not attribute the entire navigation time to interpreter startup.

## Human acceptance on the real terminal

Use an isolated sample library with ordinary shells, in the terminal and remote
path you intend to support. Test both a single pane and a four-pane layout:

1. Alternate tabs and workspaces at least twenty times by click, then by shortcut.
   Watch the selection and destination together: no full-list highlight flash,
   missing names, transient layout expansion or visible delayed selection.
2. Immediately after each navigation gesture, type a distinct harmless marker.
   Verify it reaches only the intended pane. Repeat with rapid consecutive
   gestures; ensure stale acknowledgement cannot release input to a previous pane.
3. Resize narrow/wide, repeat with two viewer windows, and disconnect/reconnect
   an attached disposable session. Preserve shell identities, cwd and split ratios.
4. Record terminal/version, dimensions, local versus actual SSH connection,
   observed behavior and all benchmark runs. PTY figures alone cannot certify
   the native display or network path.

Do not close the performance gate merely because a trace is shorter or the
numbers disappear from the README. It requires measured budget compliance,
correct input routing, preserved ownership/layout behavior, passing integration
checks and the observed interaction above.

## Existing baseline and limitations

An earlier exploratory macOS run observed **0.7–1.1% of one core at idle** for
one- and four-pane views. Those ten-second samples included the fixture process
trees and exited children, on Python 3.14.7/tmux 3.7c at 160×38 with
`xterm-256color`. This is a short sample from one development host, not a scaling
claim or cross-platform guarantee. The previous switch probe used keyboard input
only. Its raw timings remain engineering evidence; they are not mouse or native
paint measurements. Keep before/after navigation results with the performance
issue until the release gate has been met.

The repeatable harness was exercised against runtime revision `56357b0` on
macOS/Darwin 25.6.0, arm64 with ten logical CPUs, Python 3.14.7 and tmux 3.7c.
At 160×38, with two seconds of warmup, three ten-second idle samples and twenty
navigation events per group, idle measured **0.60–0.90%** for one-pane views and
**0.50–0.60%** for four-pane views. The warmed workloads held four and sixteen
ordinary shells respectively, including parked tabs: eleven and twenty-nine owned
processes stayed stable through the idle samples. Shell identities also survived
navigation, resize and attachment changes. These are one host's exploratory
results, not evidence that more panes use less CPU. The navigation gate is still
open; keep the raw event samples with the engineering issue for comparison.
