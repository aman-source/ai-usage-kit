# CLAUDE.md: ai-usage-kit (`eai`)

## What this is
A Python toolkit that cuts AI coding agent (Claude Code) token spend without changing the developer's model, seat
or workflow. One install per developer: `uv tool install ai-usage-kit`, then `eai <command>`.
Designed for an enterprise rollout (organization-managed settings respected; nothing leaves the machine).

## Status (2026-10-17)
All modules below are built, independently reviewed and merged. 691 tests; ruff and mypy strict clean.
Hook modules were verified against real Claude Code 2.1.263 with temp settings (scripts/probe_*.py).

| Module | Command | Default | What it does | Design note |
|---|---|---|---|---|
| analyze | `eai analyze [--since 30d] [--out r.md] [--export e.json]` | n/a | Reads local session logs, reports tokens and waste (repeated reads, large tool output, cache rebuilds, big instructions, many MCP servers, long sessions). `--export` is numbers only, per-export random id | docs/analyze.md |
| defaults | `eai init`, `eai status`, `eai on/off <item>`, `eai uninstall` (`--dry-run`) | compact, effort, deny-reads on | Documented Claude Code settings: `autoCompactWindow` 200000, `effortLevel` medium only if unset, `permissions.deny` for generated output. Opt-in: `deny-deps`, `cache` (API/cloud billing only) | docs/defaults.md |
| trim | `eai on trim` | opt-in | PostToolUse hook: shortens successful pytest / uv / npm / gradle-build output over 8,000 chars, saves full output locally, never touches failures or the command | docs/trim.md |
| read-once | `eai on read-once` | opt-in | Pre/PostToolUse(Read) + SessionStart hooks: denies once a re-read of an unchanged file in the same context; retry allowed; resets on compact/clear | docs/read-once.md |
| concise | `eai on concise` | opt-in | Rule file `~/.claude/rules/eai-concise.md`: shorter replies, every technical detail kept | docs/concise-minimal.md |
| minimal | `eai on minimal` | opt-in | Rule file `~/.claude/rules/eai-minimal.md`: smallest complete change, safety never cut | docs/concise-minimal.md |
| subagent-model | `eai on subagent-model` | opt-in | `env.CLAUDE_CODE_SUBAGENT_MODEL=sonnet`: helper agents that pick no model run on Sonnet; main model, Explore/Plan and agents with their own model unchanged; refused below Claude Code 2.1.251 | docs/subagent-model.md |
| mcp | `eai mcp [--since 30d] [--project P]` | n/a | Read-only: MCP tool-search state and why, servers for the project (user/local/project/organization), calls in local logs, exact advice for unused ones; never changes files | docs/mcp-check.md |
| navigate | `eai on navigate`; Claude runs `eai navigate where/who-uses/impact/outline` | opt-in | Local Python code graph (own standard-library engine) in `~/.ai-usage-kit/navigate/graphs/<id>/` (outside repos); one-call answers with `path:line`, several names per call, `*x*` patterns, `--depth` callers of callers; background rebuild when stale; optional git hooks (`eai navigate hooks on`); rule file + 8 narrow allow rules; no MCP server | docs/navigate.md |

All three roadmap items are built (see Roadmap for what is next). Deferred: large tool-result vault,
Copilot adapter.

Savings are not yet proven: every module is measured at the pilot with `eai analyze --export` before and after
(tokens and API calls per prompt; task quality reported by developers). Opt-in modules stay off unless a team's
numbers justify them.

## Hard rules
- Never change the developer's model or seat. Never overwrite a value the developer or organization set.
- Nothing leaves the machine: no telemetry, no network calls. Exports are numbers only and handed over by a person.
- Never hide errors, failing tests, security warnings or stack traces from the agent (trim only acts on successful
  commands; read-once fails open and allows a retry).
- Never break prompt caching: no rewriting earlier conversation content; settings/rules apply at session start.
- No proxy between the agent and the model provider.
- Every change is reversible: backup before the first settings change, exact `eai uninstall`, files removed only
  if unchanged since eai wrote them.
- Privacy: state under `~/.ai-usage-kit/` (or `EAI_STATE_DIR`) holds paths, hashes and counts only; folders
  0700, files 0600 on macOS/Linux; saved outputs and records expire after 7 days.

## Layout
```
src/ai_usage_kit/
  cli.py                  # argparse; `eai hook trim|read-once` is the hook entry point
  analyze/                # discover, parse, model, rules, runner, report, export
  defaults/               # settings.py (safe JSON I/O), managed.py (org settings, read-only),
                          # items.py (item table, pure apply/remove), commands.py (init/status/on/off/uninstall)
  trim/                   # filters/ (pytest, uv, npm, gradle), matching.py, hook.py, install.py
  read_once/              # state.py, hook.py, install.py
  rules/texts.py          # the concise, minimal and navigate rule texts
  mcp_check/              # eai mcp: toolsearch.py, config.py (server sources), report.py
  navigate/               # graph/ (engine), files.py, snapshot.py, store.py, build.py, freshness.py,
                          # tools.py (answers), query.py (commands), githooks.py, install.py
  hook_files.py           # shared private-file helpers for hooks
tests/                    # mirrors src; an autouse fixture points Path.home() at a temp dir
scripts/                  # check_real_totals.py, check_navigate.py, probe_*.py (trim, read-once, rules, navigate)
docs/                     # one design note per module
THIRD_PARTY_NOTICES.md    # reference repos, licenses, what informed what
```

## Develop
- `uv sync`; gates before every commit: `uv run pytest -q`, `uv run ruff check . scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy`.
- Lines <= 110 characters. Python 3.11+, standard library only at runtime; ask before adding any dependency.
- Tests must never touch the real home or real `~/.claude`. Manual checks on a real machine use `--dry-run`.
- Probe scripts (`scripts/probe_*.py`) spend Claude usage on the signed-in account; run only when approved.
- Workflow used for every module: written spec -> written plan -> test-first
  build -> independent whole-branch review -> fix all findings with tests -> real probe for hooks -> merge.

## Reference repos (read during development only; not part of this repository)
- rtk (Apache-2.0; `engine/` and `proxy/` are BUSL-1.1 and must never be read) -> trim
- caveman (Apache-2.0) -> concise; ponytail (MIT) -> minimal
- Port behavior in our own words/code; reference names never appear in shipped code, CLI output or user docs;
  record each in THIRD_PARTY_NOTICES.md with license, commit and deliberate differences.

## Known limits
- Hook behavior verified only on Windows with Claude Code 2.1.263; re-run the probes after Claude Code updates.
- `CLAUDE_CONFIG_DIR` users: rule-file location under the config dir is assumed, not verified live.
- Windows cannot restrict file permissions with chmod; state relies on the user profile's protection.
- `trim`: not for Maven, git, `pip install`, other Gradle tasks (v1 scope).

## Roadmap (for the next agent)
Build each item with the same workflow as above (spec -> plan -> test-first -> independent review -> fix all ->
real probe -> merge). Verify every Claude Code setting, hook field and MCP detail against current docs
(code.claude.com/docs) before relying on it; record what was verified in the module's design note.

### 1. navigate: built (docs/navigate.md)
Decisions taken: an own engine (standard library, Python only) instead of an external graph
binary (codebase-memory-mcp, MIT, ~160 languages: security review and unsigned-binary flags on Windows); graphs
outside repos; Claude reaches the graph through plain commands guided by a rule file, not an MCP server (works
when an organization blocks MCP servers; fewer moving parts). Evidence that a graph is not automatically cheaper
(github.com/nefayran/code-graph-vs-grep): small repos favored Read/Grep; a routing rule and one-call lean answers
are what make it pay; turns cost more than bytes. Ship decision at the pilot on large repos only.
Next options, each only if the pilot asks for it: a thin MCP server over the same tools (if Claude ignores the
rule); other languages (tree-sitter would be a dependency; or adopt codebase-memory-mcp after a security review);
links across repositories (the engine already indexes several repos in one graph).

### 2. mcp-diet: built as the read-only checker `eai mcp` (docs/mcp-check.md)
Tool search defers MCP schemas by default; the real cost is when it is off (company gateway URL, betas disabled).
eai explains and advises; it never flips tool search (a gateway without `tool_reference` support would fail).
Possible later step: read the actual deferred/loaded state from session logs (tool definitions carry
`defer_loading`) instead of inferring it from settings.

### 3. subagent-default: built as `subagent-model` (docs/subagent-model.md)
Mechanism verified in the docs: `CLAUDE_CODE_SUBAGENT_MODEL` in settings `env` (third in the model order, after the
per-call model and the agent's own `model`; first before 2.1.251, hence the version floor). Measure at the pilot.

### Rollout (company pilot)
Publish the package to the company's internal package index (Artifactory); developers run
`uv tool install ai-usage-kit`. Pilot teams: `eai analyze --export` before, `eai init` (+ opt-in items per team),
`eai analyze --export` after a week or two; compare tokens and API calls per prompt; collect quality reports.
Turn off any item whose numbers do not justify it.
