# A/B benchmark: same task with and without eai (2026-10-17)

## Setup
- Script: `scripts/bench_ab.py`. Repository: rich (Python), with history removed from each copy.
- Two real bug fixes, each started from the commit before the fix:
  - A: soft-wrap style bleeds onto the newline (fix `39ee57df`);
  - B: the panel title is missing the panel background (fix `30e5ed61`).
- Judge: the upstream fix's tests are copied in after the session. A run passes if the target test passes
  and there are no new failures compared with the fixed tree.
- 3 runs per task and side, 12 sessions in total. Model: Sonnet. Claude Code 2.1.263 on Windows. Up to 6 sessions
  ran in parallel.
- Both sides ran with the same isolation:
  - flags: `--setting-sources project,local`, an empty `--strict-mcp-config`;
  - env: `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`;
  - the same prompt, tools and turn and budget limits;
  - a fresh copy and a fresh state dir per run.
- eai side: the real output of `eai on recommended navigate`:
  - settings as the copy's `.claude/settings.local.json`;
  - rule files passed through `--append-system-prompt-file`;
  - the navigate graph built before the run.
- Baseline: every PATH folder with an `eai` executable was removed.
- How "no cheating" was checked:
  - a canary prompt asked each side what is in its context. Baseline answered "no no no no", eai "yes no no
    yes", and both sides loaded zero plugins;
  - after each run, the check confirmed no `eai` command and no eai state files on the baseline, and that a
    result event exists;
  - result: no isolation problems in the final 12 runs.

## Results (medians; cost is the `total_cost_usd` Claude Code reports)

| task | side | pass | cost | turns | seconds | cache write | cache read | output tokens | tool result chars |
|---|---|---|---|---|---|---|---|---|---|
| A | base | 3/3 | $0.88 | 36 | 255 | 54,438 | 2,303,359 | 16,534 | 62,257 |
| A | eai  | 3/3 | $0.82 | 36 | 254 | 50,396 | 2,299,100 | 15,975 | 54,936 |
| B | base | 3/3 | $0.64 | 26 | 176 | 48,656 | 1,617,427 | 11,067 | 70,121 |
| B | eai  | 3/3 | $0.48 | 19 | 129 | 44,324 | 1,120,477 | 7,178  | 62,864 |

Mean cost per run:

| task | base | eai | change |
|---|---|---|---|
| A | $0.830 | $0.755 | -9% |
| B | $0.598 | $0.481 | -20% |
| all 12 runs | $4.28 | $3.71 | -13% |

All 12 runs passed, so quality was the same on both sides.

Per-run costs:
- A base: 0.88, 0.90, 0.71
- A eai: 0.84, 0.82, 0.60
- B base: 0.65, 0.50, 0.64
- B eai: 0.48, 0.35, 0.61

## What the numbers say
- eai was cheaper on both tasks: 13% on average, with the same pass rate.
- The sample is small, 3 runs per cell, and the spread is wide: the costs of the two sides overlap. Treat
  "about 10–20% cheaper" as a signal, not a proven number.
- **Where the saving came from:** fewer turns and less output, on task B in particular (output -35%, turns 26 → 19).
  That points to the `concise` and `minimal` rules, the only items that acted in these runs.
- **What did not act:**
  - `navigate`: 0 calls in 6 eai runs. Claude used Grep, Read and Bash instead.
  - `read-once`: 0 re-reads denied.
  - `trim`: 0 outputs shortened, because the pytest runs were short.

  No conclusion about these three can be drawn from this benchmark.
- About 49k tokens of every session are system-prompt cache writes, the same on both sides. eai cannot change
  them.

## Bugs and open items left
1. **navigate is never used** on a task of this size, even with the rule loaded. Next steps:
   - find out why: rule wording, rule placement through `--append-system-prompt-file` compared with
     `~/.claude/rules`, or rich being too small for a graph to pay;
   - rerun on a larger repository before shipping navigate.
2. **read-once and trim are not measured.** They need a task with long test output and large files read twice.
3. **The benchmark is small, n=3.** Run 5 or more runs per cell, and use more tasks, before quoting a number to
   the pilot.
4. **Read and Grep outside the project are auto-denied** in `-p` mode, 0–3 times per run, on both sides. This is
   fair to both sides, but it adds turns.
5. **bench_ab.py uses `shutil.rmtree(onerror=...)`**, which is deprecated on Python 3.12, so the script is run with
   `-W ignore::DeprecationWarning`. Switch to `onexc` when Python 3.11 support is dropped.
6. **Fixed in this session, not yet committed:**
   - `eai on recommended navigate` printed "left off: navigate…" for an item that was turned on. Fixed, with a
     test in `tests/test_cli.py`.
   - The bench script now starts claude by its resolved path and sends the prompt on stdin. On Windows, cmd.exe
     cut the argument list at the first newline, which dropped the isolation flags. The first full run was
     invalid for this reason, and its results were thrown away.
   - `metrics` now skips system events whose `message` is a string. `--resume` rescores finished runs and
     launches only the missing ones.

   The gates passed after the eai fix: 696 tests, ruff and mypy clean. Still to do: commit, add bench_ab.py to the
   scripts list in CLAUDE.md, and publish through the clean export.
