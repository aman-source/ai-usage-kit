# defaults: design note

`eai init` applies documented Claude Code settings to the developer's user settings
(`~/.claude/settings.json`) and `eai uninstall` removes exactly them. Designed for an enterprise setup;
tested with a Claude Pro subscription.

## Items (names checked against code.claude.com/docs on 2026-10-16)
| Item | Setting | Value | Source |
|---|---|---|---|
| compact | `autoCompactWindow` | 200000 (allowed 100000-1000000; default: tuned per model) | settings-reference#autocompactwindow |
| effort | `effortLevel` | "medium" only when unset; `/effort` or `--effort` override per session | settings-reference#effortlevel, model-config |
| deny-reads | `permissions.deny` | `Read(<pattern>)` for generated output (`dist`, `__pycache__`, `.next`, `coverage`); gitignore syntax, relative so they match at any depth in every project | permissions (Read and Edit rules) |
| deny-deps | `permissions.deny` | opt-in: dependency folders, `build`/`target`/`vendor`, lock files | permissions |
| cache | `promptCacheTtl` | "1h"; opt-in, only with API-key/cloud billing | settings-reference#promptcachettl, prompt-caching |

Why these choices:
- Deny rules win over project settings and also block `cat`/`head`/`tail` in Bash, so only clearly
  generated output is denied by default. Dependencies and lock files are opt-in: Claude sometimes needs library
  source or a lock file (debugging, merge conflicts). `eai off deny-reads` lifts the default block.
- `.claudeignore` has no effect in Claude Code (permissions doc); deny rules are the documented way.
- Subscriptions already get a one-hour cache for the main conversation; API-key and cloud users get five
  minutes. One-hour cache writes bill at a higher rate, so `cache` only pays off for people who pause
  mid-session; `eai analyze` (cache-rebuild) shows whether that applies.
- 200000 for `autoCompactWindow` is a starting value; the pilot measures it with `eai analyze` before and after.

## Enterprise
- Organization settings win. `init` skips a value item the organization already sets (read-only check of
  `managed-settings.json`, `managed-settings.d/*.json`, and the Windows registry `Settings` value under
  HKLM/HKCU `SOFTWARE\Policies\ClaudeCode`). Deny rules merge across scopes, so ours are added alongside.
- Server-managed settings (claude.ai console) and macOS MDM profiles are not visible locally; `status` says so.

## Details
- `CLAUDE_CONFIG_DIR` is respected: user settings are `$CLAUDE_CONFIG_DIR/settings.json` when it is set.
- The Windows HKCU policy is counted only when no admin source (file or HKLM) is present, as Claude Code does.
- `cache` also counts an LLM gateway (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`) as API-style billing;
  the gateway must forward the `anthropic-beta` header for the one-hour cache to work.
- `status` checks settings.json, not only eai's own record: it reports items removed by hand (`eai init`
  restores them), values the developer changed, and per-model `modelSettings` values that override ours.
- `--dry-run` output says "would turn on" / "would remove".

## Safety
- Backups are readable by the user only (0600; state folder 0700) because settings.json can hold secrets.
- If settings.json changes while eai runs (Claude Code saves it too), eai stops without writing.
- A UTF-8 byte order mark is accepted and restored on uninstall.
- A damaged state file is a one-line error; items from older versions are still undone from their record.
- Merge by key; atomic write; invalid JSON stops with one line and changes nothing.
- Backup of the original file on the first change; `uninstall` restores it byte-for-byte when nothing else
  changed; otherwise keeps the developer's later edits and saves the original next to the settings file.
- A value the developer or organization set is never overwritten or removed.
- State lives in `~/.ai-usage-kit/` (which items are on, what we added); no identifiers.
