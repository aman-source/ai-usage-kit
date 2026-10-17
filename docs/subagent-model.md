# subagent-model

Opt-in: `eai on subagent-model`. Undo: `eai off subagent-model` (or `eai uninstall`).

## What it does

Adds one line to your user settings (`~/.claude/settings.json`):

```json
{ "env": { "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet" } }
```

Helper agents (subagents) that do not choose a model then run on Sonnet instead of your main model. Your main
model never changes. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` is never set, so an agent that names its own model keeps it.

## How Claude Code picks a subagent's model

From code.claude.com/docs/en/sub-agents (checked 2026-10-16), in order:

1. the model Claude passes when it starts the agent;
2. the agent's own `model` setting (`inherit` means the main model);
3. `CLAUDE_CODE_SUBAGENT_MODEL`, when set;
4. the main conversation's model.

So this item only affects agents that reach step 3: the general-purpose agent and custom agents without a `model`.
Built-in Explore and Plan keep their own model. Your organization's allowed-model list still applies.

Before Claude Code 2.1.251 the variable came first and overrode agents that chose their own model, including
`inherit`. `eai on subagent-model` therefore reads the newest Claude Code version from your recent local session
logs and refuses when it is older than 2.1.251. When no version is found, it is turned on.

## When it changes nothing

- Your organization sets the variable in managed settings: theirs wins.
- You already set it in your settings or in your shell environment: yours is kept.

## Limits

If your main model is Haiku, helpers would run on a larger model. Turn the item off in that case.

## Measuring

Run `eai analyze` before and after: the subagents column shows their tokens. Watch also for more turns per task,
which would mean a weaker helper costs more than it saves.
