# concise and minimal: design note

Two opt-in instruction files for Claude Code. Each is a small Markdown file eai writes to the user-level rules
folder, which Claude Code loads in every project at the start of a session.

| Item | File | What it asks of Claude |
|---|---|---|
| `concise` | `<config>/rules/eai-concise.md` | Format or reporting instructions from the user, project, a skill or the harness come first. Lead with the answer; no greetings, preambles, recaps or closing offers; say things once; between tool calls write only to warn, clarify or mark a new step; after the last call give as much detail as the answer needs. Keep code, commands, paths, names, numbers, error text and existing comments exactly. Normal, complete sentences; never drop "not", "no", "never", "only" or "except"; if shorter and clearer conflict, choose clearer. Full detail (and confirmation first for irreversible actions) for security risks, destructive actions, step order, confusion, questions to the user, and anything saved or sent. Reply in the user's language. |
| `minimal` | `<config>/rules/eai-minimal.md` | Find everything the change must update and who it could hurt (data it could delete or leak, callers it could break). Then: is it needed; reuse existing code; the standard library; a dependency the project already has; only then new code. Finish callers, tests, fixtures and configuration; fix bugs at the root cause; carry error handling along when moving code; prefer the edge-case-correct option; new non-trivial logic gets a small test; record accepted limits in a `known limit:` comment; build what was asked and name a smaller option in one line. Never cut anything the user asked for, input validation, security measures, error handling that prevents data loss or silent failures, accessibility, or tests. End each code change with one or two lines on what was left out or not verified and any risk, in addition to any other closing line. |

`<config>` is `$CLAUDE_CONFIG_DIR` if set, otherwise `~/.claude`. The developer's own `CLAUDE.md` is never edited.
Format or reporting instructions from the user, the project, a skill or the tool harness take precedence over
`concise`; both files keep every safety rule of the behavior they port (see `THIRD_PARTY_NOTICES.md`).
The full texts are in `src/ai_usage_kit/rules/texts.py`.

## Install and removal
- `eai on concise|minimal` writes the file (atomically) after saving eai's record (path and SHA-256 of the
  content). If a file with that name already exists with other content, eai refuses and changes nothing; an
  identical file is adopted.
- `eai off <item>` and `eai uninstall` delete the file only if it is still exactly what eai wrote; an edited file
  is kept and reported. `eai init` never turns these on, but restores a file eai installed that was deleted.
- `eai status` shows on, off, missing (restore with `eai init`), changed by you, or "off, but its file is still
  there" when a removal could not delete the file.
- `eai init` updates an installed file that still matches eai's record to the current text of a newer eai
  version; an edited file is never touched.
- Settings are backed up only by items that change settings; rule items never copy settings.json.
- `eai uninstall` also removes the rules folder if eai's files were the last ones in it.
- `--dry-run` shows the change and writes nothing. The settings file is never changed by these items.

## When it takes effect
Rules load at session start, so turning an item on or off applies to the next session. Nothing changes inside a
running session, so the prompt cache is never broken mid-session. Each file adds a few hundred tokens to every
session's context (read from cache after the first request).

## Why opt-in, and how to measure
Earlier billed tests of terse-output styles were flat or worse; minimal-code instructions saved about 10% in one
test. These items change how Claude talks to and codes for developers, so teams turn them on deliberately and the
pilot measures them: `eai analyze --export` before and after a period of normal use, comparing output tokens per
prompt and API calls per prompt, with developers reporting any drop in answer quality.

## Limits
- Instructions are guidance, not enforcement: Claude may not follow every rule every time.
- A project `CLAUDE.md` or project rule that asks for more detail can conflict; Claude may follow either.
- An organization's `claudeMdExcludes` setting could exclude these files; `eai status` cannot see that.
- With `CLAUDE_CONFIG_DIR` set, eai writes to `$CLAUDE_CONFIG_DIR/rules/`, assuming Claude Code reads user rules
  from its config folder as it does settings; the docs name only `~/.claude/rules/`. Not yet verified live.
- Each file adds about 550 tokens to every session (cached after the first request).
- `eai on concise|minimal` needs a readable settings.json (eai keeps one record file for all items).
