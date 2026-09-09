# Performance measurement

Switching performance was the public-release gate. The target for **each** one-
and four-pane tab/workspace navigation path is **p50 ≤ 150 ms and p95 ≤ 250 ms**,
for both clicks and direct shortcuts, met for both shell-client readiness and the
routed-input acknowledgement in repeated untraced runs. These are engineering
acceptance budgets, not a guarantee about any particular machine. Three quiet-host
runs met them; [the result](#release-gate-result) records what that does and does
not establish. Timing is deliberately not asserted by ordinary unit tests.

## Run on disposable terminals

From the checkout, with Python and tmux available:

```sh
mkdir -p .backbone/benchmarks
python3 -m scripts.benchmark > .backbone/benchmarks/baseline.json
python3 -m scripts.benchmark --viewers 2 > .backbone/benchmarks/two-viewers.json
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
python3 -m scripts.benchmark --panes 1 4 --samples 2 \
  --idle-samples 1 --idle-seconds 1 --warmup .2
```

`make benchmark` runs the same module; pass options through `BENCHMARK_ARGS`.
The module entry point uses the normal package imports, without modifying
`sys.path`. Use `--help` for durations, sample counts and polling controls. Navigation sample
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
python3 -m scripts.benchmark --panes 1 4 --samples 2 \
  --idle-samples 1 --idle-seconds 1 --trace-commands \
  > .backbone/benchmarks/commands.json
```

Tracing adds a temporary shell wrapper only to the fixture's PATH. It counts
application tmux invocations separately from the observer's direct tmux queries.
This wrapper adds process and file-I/O cost: compare traced runs only with traced
runs, and use **untraced** runs for the latency gate. Batched tmux subcommands
count as one invocation. The separate `helper_import_baseline` runs five fresh
interpreters importing `tmux_workspaces.cli` and exiting, without actions or tmux
work. It characterizes interpreter/CLI startup, not the cost of an actual
`_action` request or individual rendering stages. The main viewer stays alive; keyboard
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

Never call the switching budgets met because a trace is shorter or the numbers
disappear from the README. The claim requires measured budget compliance, correct
input routing, preserved ownership/layout behavior, passing integration checks and
the observed interaction above. The same bar applies to any later change that
alters navigation, rendering or attachment.

## Existing baseline and limitations

An earlier exploratory macOS run observed **0.7–1.1% of one core at idle** for
one- and four-pane views. Those ten-second samples included the fixture process
trees and exited children, on Python 3.14.7/tmux 3.7c at 160×38 with
`xterm-256color`. This is a short sample from one development host, not a scaling
claim or cross-platform guarantee. The previous switch probe used keyboard input
only. Its raw timings remain engineering evidence; they are not mouse or native
paint measurements. The before/after navigation results are kept with the
performance issue.

The repeatable harness was exercised against runtime revision `56357b0` on
macOS/Darwin 25.6.0, arm64 with ten logical CPUs, Python 3.14.7 and tmux 3.7c.
At 160×38, with two seconds of warmup, three ten-second idle samples and twenty
navigation events per group, idle measured **0.60–0.90%** for one-pane views and
**0.50–0.60%** for four-pane views. The warmed workloads held four and sixteen
ordinary shells respectively, including parked tabs: eleven and twenty-nine owned
processes stayed stable through the idle samples. Shell identities also survived
navigation, resize and attachment changes. These are one host's exploratory
results, not evidence that more panes use less CPU. The navigation gate was still
open at this revision; the raw event samples are kept with the engineering issue.

## Repeated packaged baseline

Three clean, untraced runs at `6abcad8` used the command below, with thirty events
per path per run and three ten-second idle samples per pane count:

```sh
python3 -m scripts.benchmark --panes 1 4 --samples 30 \
  --idle-samples 3 --idle-seconds 10 --warmup 2
```

The environment was Darwin 25.6.0, arm64, ten logical CPUs, Python 3.14.7 and
tmux 3.7c, at 160×38 with `xterm-256color`. Background load and power settings
were not controlled. All shell identity, post-readiness input uniqueness, resize
and attachment probes passed. These are local PTY measurements, not SSH or
native display measurements.

Valid idle samples had weighted means of 0.797% of one CPU for one pane and
0.843% for four panes. One of nine one-pane CPU samples was invalidated for
process-set churn and retained in the raw evidence; all nine four-pane samples
were valid. No run or latency sample was discarded. At this revision, only the
one-pane click-tab path met both navigation budgets in every run.

An independent same-write gesture-and-typing stress test found an existing mouse
input loss on the baseline: readiness probes alone do not establish immediate
typing correctness. This is tracked separately in issue #17 and must be fixed
and included in the optimized comparison. Keep the raw baseline runs and final
comparison with the engineering review; timing eligibility is not acceptance.

## Reviewed navigation implementation

Actions wake the persistent controller directly instead of waiting for its
150 ms fallback poll. Pane state is shared only within one event and invalidated
before focus or layout mutations. Shell discovery, cwd capture and pane metadata
use bounded tmux batches. Name-only updates reuse healthy attachment clients;
changed layouts and dead clients still rebuild. Newline-containing cwd values,
fragmented Unicode input and small-screen layouts retain their regression coverage.

Sidebar mouse gestures now use the same applied-action acknowledgement as direct
shortcuts. Subsequent terminal input waits for navigation and focus to finish.
The separate same-write/burst PTY tests cover tabs and workspaces with one and four
panes, along with double-click editing, native content mouse input and bracketed
paste. This corrects the baseline lost-input finding described above.

A controlled comparison used the same benchmark code and settings at packaged
baseline `6abcad8` and isolated optimized revision `56cc8fb`, with three serial
runs and 30 events per path per run. All 720 optimized post-readiness input probes
were unique, and shell identities survived. All 18 optimized idle samples were
valid: weighted idle CPU was 0.629% of one core for one pane and 0.706% for four,
compared with 0.797% and 0.843% on the baseline. The host and scope are as above.

Separate diagnostic traces reduced application tmux invocations per navigation
from 12–14 to 6–7 for one pane, and from 39 to 12–13 for four panes. A batch counts
as one invocation, not as one internal tmux operation. Traced execution is never
used as acceptance timing.

The comparison improved shortcut and four-pane click latency; one-pane clicks
became slower while still meeting the targets. All one-pane paths met both
budgets in every optimized run, but four-pane results did not. The two builds
include multiple changes, so this comparison does not isolate the cause of the
one-pane click tradeoff. Raw per-run latency and remaining gaps are recorded on
[the performance gate](https://github.com/eandualem/tmux-workspaces/issues/3).
Background host workload and power settings were uncontrolled; no observer cost
was subtracted and no slow sample was discarded.

The comparison isolates the performance changes and predates integration with
layout validation, provider isolation, shell context and final lifecycle fixes.
It must not be presented as a measurement of final main. A small integrated probe
checks the combined revision separately; it cannot replace the repeated budgets
or native terminal visual acceptance. The budgets were met at a later revision;
see [the result](#release-gate-result).

## Release gate result

The budgets were met at geometry-reuse revision `fad3d125`, whose tree is the
merged `4d3d213`. Three untraced runs on a quiet host, thirty events per path per
run, passed **every** budget on **every** path — one and four panes, clicks and
shortcuts, readiness and routed acknowledgement separately. Ninety events per path
per pane count; all 720 post-readiness routing probes were unique and every shell
identity survived. Per-run figures and the paired baseline comparison are recorded
on [the performance gate](https://github.com/eandualem/tmux-workspaces/issues/3).

Considerable work landed afterwards — keymaps, startup checks, packaging, keyboard
menus, viewer themes and explicit refresh — so the release head was re-measured
against `4d3d213` directly. Runs alternated between the two revisions so that host
load fell on both equally, three runs each, identical benchmark source and options.
Pooled over ninety events per path, the release head was faster at the median on
seven of eight paths, by 1.2% to 25.8%. The single median regression, four-pane
shortcut-workspace at +8.9%, came with a 5.3% p95 improvement. The later work did
not slow navigation.

That comparison ran on a host under heavy competing load, where **neither**
revision meets the budgets: it establishes the absence of a regression, not
compliance. Do not quote its absolute milliseconds as a result. Budget compliance
rests on the quiet-host runs above.

### What this does not establish

Every figure is a PTY measurement on Apple silicon. It is not native Ghostty
paint, not a frame-completion time, not an SSH or WSL measurement, and not a
result for slower or loaded machines. Readiness and routing probes do not by
themselves prove that typing immediately alongside a click is safe; the PTY
same-write and burst suites cover that case, and the owner's observation of the
running application covers the visual behavior the numbers cannot describe.
Re-run the [human acceptance procedure](#human-acceptance-on-the-real-terminal)
when navigation, rendering or attachment code changes materially.
