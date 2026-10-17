# ai-usage-kit

Cut Claude Code token spend without changing your model, your seat or how you work.
Everything runs locally; nothing is sent anywhere.

```bash
uv tool install ai-usage-kit      # or from a checkout: uv tool install .
uv tool update-shell              # once, if uv says its tool folder is not on PATH
eai analyze --since 30d           # where did my tokens go?
eai init --dry-run                # preview the default settings
eai init                          # apply them (backup first; exact undo)
eai status                        # what is on, off, or set by your organization
eai mcp                           # do MCP servers cost you tokens? (read-only)
eai navigate who-uses price       # (after eai on navigate) callers from the local code graph
eai on trim                       # opt-in: trim, read-once, concise, minimal, navigate, subagent-model, deny-deps, cache
eai uninstall                     # put everything back exactly
```

| Module | Default | What it does |
|---|---|---|
| analyze | n/a | Report tokens and waste from local Claude Code session logs; optional numbers-only export |
| defaults | on | Earlier auto-compaction, medium effort only if you chose none, no reads of generated output |
| trim | opt-in | Shorter output from successful test, install and build commands; full output kept locally |
| read-once | opt-in | Skip re-sending a file Claude already read and that has not changed |
| concise | opt-in | Rule file for shorter replies; every technical detail kept |
| minimal | opt-in | Rule file for the smallest complete code change; safety never cut |
| subagent-model | opt-in | Helper agents that pick no model run on Sonnet; main model unchanged |
| mcp | n/a | Read-only check: MCP tool search state, unused servers, exact fixes |
| navigate | opt-in | Local Python code graph: Claude runs `eai navigate where / who-uses / impact / outline` instead of grep-and-read |

Savings depend on how you work: measure with `eai analyze --export` before and after.
Design notes for each module are in `docs/`. Development: see `CLAUDE.md`.
Third-party behavior this project ports is credited in `THIRD_PARTY_NOTICES.md`.

Requires Python 3.11+. No runtime dependencies.
