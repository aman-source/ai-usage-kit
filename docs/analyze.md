# analyze: design note

## What it does
`eai analyze` reads local Claude Code session logs and reports where tokens went. It changes nothing.
It is the "before" baseline every later module is measured against.

## Reference tool
None. `analyze` is not ported from a reference repo, so there are no parity tests. Accuracy is checked by unit
tests on hand-built fixtures and by `scripts/check_real_totals.py`, which compares totals against an independent
count on real logs.

## Log format facts (Claude Code 2.1.263-2.1.296, observed 2026-10-16)
- Main transcript `<root>/<project>/<session>.jsonl`; subagents `<root>/<project>/<session>/subagents/*.jsonl`.
- One API response is split across several `assistant` records sharing `message.id`. Each repeats `usage`;
  streaming updates `output_tokens`, so the last record's usage is used. Summing per record about doubles tokens.
- `<synthetic>` model marks messages that were not API calls; excluded.
- Compaction: `system` with `subtype: compact_boundary`; the summary arrives as `user` with `isCompactSummary`.
- MCP servers are announced in `deferred_tools_delta` / `deferred_tools_record` attachments as
  `mcp__<server>__<tool>` names.

## Rules and decisions
| Rule | Fires when | Waste | Fix |
|---|---|---|---|
| repeated-read | Same `Read` (thread, path, offset, limit) again with no Edit/Write/MultiEdit/NotebookEdit to the path in between | chars of the repeat; est. tokens = chars // 4 | read-once |
| large-tool-output | One tool result > 20,000 chars | chars; est. | trim |
| cache-rebuild | Main-thread gap > 5 min and cache read < 50% of previous call's context | cache write of that call (exact) | defaults |
| big-instructions | CLAUDE.md at a session cwd, `<cwd>/.claude/CLAUDE.md` or `~/.claude/CLAUDE.md` > 200 lines; checked once per file | lines, bytes (on disk today) | defaults |
| many-mcp | > 3 MCP servers announced | count, first-call context | defaults (mcp-diet if justified) |
| long-session | > 200 main API calls or peak context > 150k | cache read on calls above 150k (exact) | defaults |

Decisions:
- Reads in different threads are separate contexts and are never repeats of each other.
- A read of a different range of the same file is not a repeat. No offset, offset 0 and offset 1 all mean line 1.
- A Bash command can change a file without the analyzer knowing; repeated-read may over-count in that case.
- `mcp_instructions_delta.addedNames` uses other naming formats (`plugin:x:y`, `claude.ai Name`) and is ignored
  to avoid double counting.
- cache-rebuild needs cache write > 0: a call that neither reads nor writes cache rebuilt nothing.
- Bash output is labelled by the first word of the command; `cd x && pytest` shows as `Bash:cd`.
- User prompts: `origin.kind == "human"` when `origin` exists (task notifications carry
  `origin.kind == "task-notification"`). Without `origin`, typed text minus generated prefixes
  (`<task-notification>`, `<local-command-stdout>`, `<local-command-caveat>`, `[Request interrupted`).
- Sessions with no non-synthetic API calls are skipped.
- Tokens only; no dollar figures.

## Export
`--export` writes numbers and fixed labels only, for an org baseline that a developer chooses to hand over.
- Tools: Claude Code built-in names only; anything else is `other`. MCP servers are a count, never names.
- Models: a plain `claude-...` name, after stripping Bedrock region/vendor prefixes and version suffixes, Vertex
  `@date` and `[1m]`. Anything else (for example cloud ARNs, which carry account ids) is `other`.
- Dates are day granularity.
- Each export gets a fresh random `export_id`. There is no persistent install or user id, and the tool writes
  nothing to the home directory, so exports cannot be linked to one person by the tool. (Compliance decision,
  2026-10-16: a persistent id is a pseudonymous identifier.)

## Markdown report
`--out` is for the developer only. It names local paths, projects and session ids, and its first line says so.
