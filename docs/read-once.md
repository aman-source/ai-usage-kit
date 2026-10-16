# read-once: design note

`eai on read-once` adds Claude Code hooks (user settings) that skip re-sending a file Claude already has.
Off by default. `eai off read-once` / `eai uninstall` remove every handler running `"<eai>" hook read-once` and
delete read-once's saved records.

## Why
Claude Code does not deduplicate reads: in 513 real `Read` results on a developer laptop none said the file was
unchanged, and the docs describe no such behavior. Every re-read sends the whole file again.

## When a read is denied (all must hold)
1. Same conversation context: same session, same subagent (`agent_id`; the main conversation is its own context).
2. Same file (absolute path; relative paths resolve against the hook's `cwd`) and same `offset`, `limit`, `pages`
   as an earlier successful read since the last compaction or `/clear`.
3. Unchanged: same size, modification time and SHA-256 as recorded at that read.
4. A plain file: not an image, PDF or notebook, at most 2 MB.
5. That exact read has not already been denied once.

Claude sees: "<path> is unchanged since you read it earlier in this conversation (same lines). If that earlier
result is still visible to you, use it. If it is not (cleared, rewound or compacted), read it again now and it will
be allowed."

A read is recorded only if the file's fingerprint taken just before the read equals the one taken just after it,
so a record always matches what Claude actually saw (a log growing during the read is never recorded).

## Never denied
Any change to the file (Claude's Edit/Write, Bash, other programs; a touch alone counts), another range, another
subagent or session, images/PDFs/notebooks, files over 2 MB, missing files and directories, the retry right after a
denial, anything after compaction or `/clear`. If anything goes wrong (bad input, unreadable or damaged records) the
hook says nothing and the read proceeds.

## Records
`~/.ai-usage-kit/read-once/<session>/<agent>/<key-hash>.json` (or under `EAI_STATE_DIR`): one small file per
file-and-range read, holding size, modification time, SHA-256, time and whether it was denied once; plus
`pending/<tool_use_id>.json` holding the fingerprint taken just before a read in progress. Paths (hashed into file
names) and hashes only; never file content. One file per read means parallel reads never overwrite each other.
Compliance note: a stored SHA-256 of a very small file (for example a one-line secret Claude was allowed to read)
could confirm a guessed value to someone who can read these private files; the transcript already holds the
content itself, so this adds little, and records expire after 7 days. Folders 0700, files 0600 on macOS/Linux; on Windows the
user profile's permissions apply. Records older than 7 days are deleted whenever the hook records a read or
`eai status` runs. Counts (`read-once/counts.jsonl`) hold day and bytes not re-sent, nothing else; rows older
than 90 days are pruned.

## Hooks installed
| Event | Matcher | What it does |
|---|---|---|
| `PreToolUse` | `Read` | Deny once when all conditions hold |
| `PostToolUse` | `Read` | Record the successful read |
| `SessionStart` | `compact\|clear` | Forget the session (all agents) |

Same refusals as `trim`: the organization allows only managed hooks, hooks are disabled, or `eai` is not a
permanent install (`uv tool install ai-usage-kit`).

## Limits
- A live `/compact` is not driven by the real probe (it cannot be triggered from `claude -p`); the reset is covered
  by unit tests and the documented `SessionStart` `compact` source.
- A rewind (`/rewind`, checkpoint restore) or Claude Code dropping old tool results without compacting fires no
  hook. The denial message tells Claude to read again if the earlier result is not visible, and the retry is
  allowed, so the cost is at most one short extra turn.
