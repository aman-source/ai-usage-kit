# navigate

Opt-in: `eai on navigate`. Undo: `eai off navigate` (or `eai uninstall`). Python git repositories only.

## What it does

Claude usually answers "where is X defined", "who calls X" and "what does this change break" by grepping and then
reading whole files. With navigate on, Claude runs one command that answers from a local code graph with exact
lines, then reads only those lines:

```
eai navigate where NAME          definition with its line span: src/shop/core.py:12-13  function  shop.core.price
eai navigate who-uses NAME       callers: path:line, confidence, kind and the line itself
eai navigate impact NAME         affected files with lines, and the tests that reach them
git diff | eai navigate impact --diff -
eai navigate outline PATH        definitions of one file with line spans, without reading it
```

Like grep, one call can ask about several things: `NAME` may be `a|b|c` (up to 10 names) and may contain `*`/`?`
(`*price*`). `who-uses --depth 2` (or 3) follows the graph: under each caller, the callers of that caller. Pages
hold 40 results (`--page 2`); a reply is at most 20,000 characters.

`eai on navigate` adds:
- the rule file `~/.claude/rules/eai-navigate.md`, telling Claude to use these commands before Grep and to read
  only the returned lines (a benchmark found graph tools are used unreliably without such a rule);
- allow rules in `~/.claude/settings.json` so Claude runs them without asking: `Bash(eai navigate where *)`,
  `who-uses`, `impact`, `outline`, and the same four for `PowerShell(...)`. Only these read-only commands.
`eai` must be a permanent install on PATH (`uv tool install ai-usage-kit`, then `uv tool update-shell`), because
Claude runs it by name. If the organization allows only its own permission rules, `eai on navigate` refuses.

There is no server and nothing listens on a port. No MCP server is needed: Claude already runs commands.

## The graph

Built from the repository's Python files with Python's own parser, by a standard-library engine in
`src/ai_usage_kit/navigate/graph/`: definitions with line spans, imports and re-exports (including `__all__` and
star imports), calls through `self`, `super()` and typed attributes, subclassing, decorators, type annotations,
FastAPI/Flask routes linked to httpx/requests calls, and which tests reach a symbol. Every result carries a
confidence: `precise` (resolved), `inferred` (name or type match) or `http` (route matched to a call).

Files: those git lists (`git ls-files`: tracked, plus untracked files that are not ignored), so `.gitignore` is
respected. Never read: secret-looking files (`.env*`, keys, certificates, `*secret*`, `*credential*`, ...) and
files over 2 MB. Files that do not parse are skipped and counted.

## Where graphs live

Outside every repository: `~/.ai-usage-kit/navigate/graphs/<id>/` (or under `EAI_STATE_DIR`), where `<id>` comes
from the repository's git folder, so each clone and each worktree has its own graph. A graph is a SQLite file:
definitions in an indexed table, references in compressed chunks per module and per attribute name, and small
compressed sections; a query opens it read-only and loads only what it needs. A new build writes a new file and
switches a pointer, so a reader never sees a half-written
graph and a file in use is never replaced (Windows). Graphs of repositories that no longer exist, or that were
not used for 90 days, are deleted automatically; `eai navigate clean` deletes the current one; `eai off navigate`
deletes all.

## Freshness

Every answer asks git whether anything changed since the build: HEAD, and the uncommitted Python changes
(`git status`) with their size and modification time. If anything changed, the answer still comes from the graph, with a line `stale: true ... changed since: <files>;
read those directly`, and a rebuild starts in the background (at most once every 10 seconds). The first question
in a repository starts the first build and waits up to 15 seconds; a very large repository answers "graph is being
built" until it is ready, and Claude uses Grep meanwhile.

Optional git hooks rebuild right after `git pull`, a branch switch or a rebase: `eai navigate hooks on` in a
repository (`off`, `status`). They add a marked block to `post-merge`, `post-checkout` and `post-rewrite` that
starts the build in the background and always succeeds, so git is never blocked. Existing hooks are kept (the block
goes right after their `#!` line); hooks that are not shell scripts are skipped; a hooks folder inside the
repository (tracked hooks, such as Husky) is never touched and you get the line to add yourself. Repositories with
hooks are recorded, so `eai off navigate` and `eai uninstall` remove them everywhere.

Builds run in a separate process with no console window, one at a time per working tree (a lock that is replaced
if its process is gone), and never print source code anywhere (the build log keeps counts and error types only).

## Limits

- Python only. Dynamic code (`getattr`, reflection, string-built names) is not resolved; such uses are missed or
  labelled `inferred`.
- One repository per graph; links between repositories are not followed.
- Every command loads the graph from disk (there is no long-running process), so answers on very large
  repositories take longer; see the measured numbers below.
- Whether Claude uses the commands depends on the rule file; measure at the pilot with `eai analyze` (Read and
  Grep calls, input tokens and turns per task) before and after.

## Measured (2026-10-16, Windows laptop, Python 3.12; `scripts/check_navigate.py`)

| Code base | Files / lines | Build | Peak memory | Graph file | `where` | `who-uses` | Precision | Recall |
|---|---|---|---|---|---|---|---|---|
| Python standard library (`Lib/`) | 1,772 / 871k | 53 s | 223 MB | 37 MB | 0.64 s | 0.33 s | 30/30 | 113/115 (98%) |
| this repository | 136 / 14k | 1.8 s | 7 MB | 1.1 MB | 0.31 s | 0.31 s | 95/95 | 73/73 |

Query times are for a fresh `eai` process, graph opening and freshness check included. A query reads only the
definitions and reference chunks it needs: the first version loaded the whole graph and took 1.7 s and 5.4 s on
the standard library. Precision: every `precise` use is on a line that names the target (or a local import alias
of it). Recall: plain `name(` calls (found with Python's tokenizer, so not in strings or comments) of module-level
functions with a unique, non-builtin name that `who-uses` returns; the two misses are dynamic calls. The
fixture tests in `tests/navigate/` are the exact answer keys.

Size and memory grow with the code base: roughly 45 bytes of graph per line of code and a build peak of about
250 MB per million lines, in a background process.
