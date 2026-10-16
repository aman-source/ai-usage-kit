# trim: design note

`eai on trim` adds a Claude Code `PostToolUse` hook (user settings) that shortens the output of successful, noisy
commands. Off by default. `eai off trim` / `eai uninstall` remove every handler that runs our hook command,
wherever it sits, and drop a hook group only when that leaves it empty.

## When it acts (all must hold)
- Event `PostToolUse` for the `Bash` tool. Failed commands arrive as `PostToolUseFailure`, which trim never handles,
  so error output is never changed. Checked against real Claude Code (2.1.263) by `scripts/probe_trim_hook.py`.
- The command starts with one of these prefixes, with no `|`, `&`, `;`, redirects, backticks or `$(`:
  `pytest`, `py.test`, `python -m pytest`, `python3 -m pytest`, `py -m pytest`, `uv run pytest`, `uv sync`,
  `uv pip install`, `npm i…` (install, i, ci), `npm ci`, `npm run …`, `./gradlew …`, `gradlew …`,
  `gradlew.bat …`, `gradle …`. Claude Code starts the hook only for these same prefixes (the hook entry's `if`
  rules are generated from the same list), so the two never disagree. Commands with `VAR=value` in front or a path
  such as `.venv/bin/pytest` are not trimmed.
- Pytest runs that ask to see more than results are left alone: `-s`, `-l`, `-V`, `-h` (also inside clusters
  such as `-vs`), `-r` with `A` or `P` (`-rA`, `-r A`), `--capture*`, `--collect-only`, `--co`, `--durations*`,
  `--fixtures*`, `--setup*`, `--log-cli*`, `-o log_cli=…`, `--cov*`, `--benchmark*`,
  `--hypothesis-show-statistics`, `--pdb`, `--trace`, `--showlocals`, `--markers`, `--help`, `--version`.
  Values of `-k`, `-m`, `-p`, `-W`, `-c`, `-n`, `--rootdir`, `--basetemp` are not read as flags.
- Gradle: only when every task named is a plain build task (`build`, `assemble*`, `bundle*`, `install*`,
  `clean`, also as `:module:task`); never with `--stacktrace`, `--info`, `--debug`, `--full-stacktrace` or
  `--warning-mode`. The value after `-x` / `--exclude-task` is not a task. Tasks whose output is the point
  (`buildEnvironment`, `help`, custom report tasks) are not trimmed.
- Output over 8,000 characters, the result at least 30% shorter, not empty, and (pytest) not
  "No tests collected".

## What the agent sees
The filtered text, then `[eai trim: showed X of Y lines; full output: <path>]` (forward slashes, usable by
Claude's Read tool and by Git Bash). Claude Code merges a command's stderr into stdout before hooks see it
(measured: `stderr` arrives empty, lines end in `\r\n`); trim filters stdout and never changes stderr.

## Saved full output
`~/.ai-usage-kit/trim/<session>/<tool_use_id>.log` (or under `EAI_STATE_DIR`). On macOS/Linux the folder is
0700 and files 0600; on Windows `chmod` cannot restrict access, so files rely on the user profile's permissions.
Logs older than 7 days are deleted whenever trim runs and whenever `eai status` runs; `eai off trim` and
`eai uninstall` delete them all. Counts (`trim/counts.jsonl`) hold day, command family and character counts only.

## Filters and deliberate differences from the reference behavior
| Command | Kept | Dropped | Our difference |
|---|---|---|---|
| pytest | summary with counts and seconds; xfail/xpass lines (10 max); failures (10 max, 3 key lines) | progress lines (including `-v` test names), headers | kept as well: warnings section (30 lines max), `SKIPPED` reasons (10 max), coverage table through `TOTAL` and "Required test coverage" (60 lines max); no pointer to the reference's own log file (ours is in the footer) |
| uv sync / pip install | install summary and package lines (20 max) | blank, Downloading, Using cached, Preparing; "Audited N packages" becomes `ok (up to date)` | none |
| npm install/ci/run | everything else | `> pkg@x` banners, `npm notice`, progress glyphs, blank lines | `npm WARN` kept (deduplicated, 20 max) |
| gradle build | BUILD status, actionable-tasks line, errors, warnings, build-scan links, blank lines | task lines, daemon/configure lines, progress, "Try" section | trims only plain build tasks (see above) |

`pip install` is not trimmed (the reference passes it through). Maven, git, other Gradle tasks: not trimmed in v1.

## Safety
- Fails open: invalid input, unexpected shapes, any exception → no reply, original output shown.
- The command itself is never changed.
- `eai on trim` refuses when the organization allows only managed hooks (`allowManagedHooksOnly`), when hooks
  are disabled (`disableAllHooks`, managed or user settings), and when `eai` is not a permanent install
  (`uv tool install ai-usage-kit`): a copy inside a project `.venv`, a virtual environment, `site-packages`
  or uv's cache (uvx) would disappear and leave a broken hook.
- The hook command quotes the executable path and escapes `"`, `$`, `` ` `` and `\`.
