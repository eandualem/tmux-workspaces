---
name: performance-investigation
description: Investigate tmux-workspaces slowness or resource use safely, for example when the viewer feels slow while many agents or sessions run. Use before measuring responsiveness, CPU, polling or tmux and subprocess cost, and when verifying a performance fix.
---

# Safe performance investigation

The sessions and agents on a development host do real work. Study the load
they already create; never create load with them.

## Never

- Start, restart, stop, message, steer or resize live agents, Backbone or
  sessions you did not create, or load-test them to manufacture concurrency.
  That needs separate owner approval.
- Send keys, clicks or resizes to a viewer you did not start, or attach a
  profiler or debugger to it.
- Edit, or switch branches in, a checkout that running viewers use. They start
  their key and pane helpers (`run _action`, `run _leaf`) from it on every
  keystroke. Work in a separate worktree.
- Stop unrelated processes to get quieter numbers. Record the host load instead.

## Order of work

1. **Read the code path.** For one key press, one tab switch and one idle
   second, list every process start, tmux round trip, poll and copy, with its
   file and line. Note what you expect each to cost before you measure it.
2. **Read existing evidence**: `docs/PERFORMANCE.md`, earlier benchmark JSON
   and local memory evidence.
3. **Observe the natural load passively** (read-only):
   - `uptime`, `sysctl vm.swapusage`, `ps -Ao pcpu,rss,comm | sort -rn | head`
   - a viewer process's cumulative CPU: `ps -o time= -p PID`, sampled twice,
     a minute apart
   - read-only queries on a viewer's private socket (`list-panes`,
     `list-clients`), and a read-only GET of the roster the viewer itself reads
4. **Measure only in isolated fixtures**, and only for numbers the code path
   cannot settle. Use private tmux sockets, a fresh library and `HOME`,
   disposable sessions, and a fake loopback roster (`--backbone
   --backbone-data-dir EMPTY_DIR --url http://127.0.0.1:PORT`).
   `scripts/benchmark.py` covers navigation between ordinary shells. Include
   an ordinary shell tab so the shell server exists, as it does in real use.
   Keep synthetic load small, because the real agents share the host.
5. **Compare before and after**: a baseline worktree and the branch worktree,
   the same options, interleaved runs, and the load average recorded with each
   run. To see where a switch spends its time, put a timestamped `tmux` wrapper
   first on the fixture's `PATH`. The wrapper slows each call, so use traced
   runs for order and gaps only, never as latency.

## Report

- Separate measured causes from hypotheses. Retract a finding that a
  measurement contradicts.
- Give the host, versions, load, raw samples and medians before and after, and
  say what the numbers do not cover: native paint, SSH, other hosts and
  heavier load.
