"""Is MCP tool search on? Rules from code.claude.com/docs/en/mcp (Configure tool search), checked 2026-10-16.

With tool search on, only tool names and server instructions load at session start; schemas load on demand.
With it off, every tool schema of every connected server is sent with every request.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

FIRST_PARTY_HOSTS = ("api.anthropic.com",)


@dataclass(frozen=True)
class ToolSearch:
    state: str  # on | off | threshold
    reason: str


def effective_env(environ: Mapping[str, str], blocks: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """The shell environment overlaid with settings `env` blocks, lowest precedence first."""
    env = dict(environ)
    for block in blocks:
        env.update({k: v for k, v in block.items() if isinstance(v, str)})
    return env


def tool_search(env: Mapping[str, str]) -> ToolSearch:
    if _truthy(env.get("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS")):
        return ToolSearch("off", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS is set")
    value = env.get("ENABLE_TOOL_SEARCH", "").strip().lower()
    if value in ("false", "0"):
        return ToolSearch("off", "ENABLE_TOOL_SEARCH=false")
    if value == "true":
        return ToolSearch("on", "ENABLE_TOOL_SEARCH=true (a gateway must forward tool_reference blocks)")
    if value == "auto" or value.startswith("auto:"):
        percent = value.partition(":")[2] or "10"
        reason = f"ENABLE_TOOL_SEARCH={value}: tools load upfront below {percent}% of the context window"
        return ToolSearch("threshold", reason)
    base = env.get("ANTHROPIC_BASE_URL", "").strip()
    if base and (urlparse(base).hostname or "").lower() not in FIRST_PARTY_HOSTS:
        return ToolSearch("off", "requests go through a custom gateway URL (ANTHROPIC_BASE_URL)")
    if _truthy(env.get("CLAUDE_CODE_USE_FOUNDRY")):
        return ToolSearch("on", "default (on Microsoft Foundry hosted on Azure it is off)")
    return ToolSearch("on", "default")


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in ("", "0", "false")
