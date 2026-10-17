# eai mcp

Read-only check of what MCP servers cost you: `eai mcp [--since 30d] [--project PATH]`. It never changes a file.

## What it shows

1. **Tool search state**, with the reason. With tool search on (the default), only tool names and server
   instructions load at session start; tool schemas load when Claude needs them. With it off, every tool schema of
   every connected server is sent with every request, so unused servers cost the most.
2. **Servers that apply to this project**: name, scope (user, local, project, organization), transport, and
   `alwaysLoad` (its tools always load, whatever tool search does).
3. **Calls in the period**, counted from your local session logs (all projects).
4. **Advice** for each unused server you control: turn it off for this project with `/mcp` (select it, Disable), or
   remove it with `claude mcp remove <name> --scope <scope>`. Servers your organization manages, and servers already
   disabled for this project, get no advice.

Servers from plugins and claude.ai connectors are not in these files; `/mcp` in Claude Code lists them.

## When tool search is off (code.claude.com/docs/en/mcp, checked 2026-10-16)

- `ENABLE_TOOL_SEARCH=false`;
- `ANTHROPIC_BASE_URL` points to a host other than api.anthropic.com (a company gateway) and
  `ENABLE_TOOL_SEARCH` is not set: most proxies do not forward the blocks tool search needs;
- `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` is set;
- Microsoft Foundry deployments hosted on Azure; models older than the Claude 4 family.
`ENABLE_TOOL_SEARCH=auto` (or `auto:N`) loads tools upfront until they reach 10% (or N%) of the context window.

eai only explains this. It never turns tool search on: through a gateway that does not support it, requests fail.
Ask the gateway owner whether it forwards `tool_reference` blocks before setting `ENABLE_TOOL_SEARCH=true`.

The state is computed from your shell environment and the `env` blocks of user, project, local and managed settings
(in that order of precedence). The gateway URL itself is never printed.

## Where servers are read from

- user scope: `~/.claude.json` `mcpServers` (or `$CLAUDE_CONFIG_DIR/.claude.json`);
- local scope: `~/.claude.json` `projects.<project path>.mcpServers`;
- project scope: `<project>/.mcp.json`;
- organization: `managed-mcp.json` (Windows `C:\Program Files\ClaudeCode\`, macOS
  `/Library/Application Support/ClaudeCode/`, Linux `/etc/claude-code/`) and the `managedMcpServers` managed setting.
Per-project choices: `disabledMcpServers` (turned off in `/mcp`) and `disabledMcpjsonServers` (rejected `.mcp.json`
servers). Server `env` values and headers are never read into the report.

Calls are matched by tool name (`mcp__<server>__<tool>`; characters other than letters, digits, `_` and `-` in the
server name become `_`). Tool definitions that appear in logs are not counted as calls.
