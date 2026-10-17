"""Render the eai mcp report."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ai_usage_kit.mcp_check.config import Server
from ai_usage_kit.mcp_check.toolsearch import ToolSearch

# Claude Code builds tool names as mcp__<server>__<tool>; other characters in <server> become "_".
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
ALL_SCHEMAS = (
    "every tool schema of every server is sent with every request; unused servers cost the most here"
)


def tool_name_server(name: str) -> str:
    return _UNSAFE.sub("_", name)


def usage_by_server(counts: Mapping[str, int]) -> dict[str, int]:
    """`analyze` tool groups (`mcp:<server>`) -> calls per server."""
    out: dict[str, int] = {}
    for group, n in counts.items():
        if group.startswith("mcp:"):
            out[group[4:]] = out.get(group[4:], 0) + n
    return out


def render(
    search: ToolSearch,
    servers: list[Server],
    here: Mapping[str, int],
    everywhere: Mapping[str, int],
    notes: list[str],
    days: int,
) -> list[str]:
    """`here`: calls in this project's sessions; `everywhere`: calls in all projects' sessions."""
    lines = [f"Tool search: {search.state} ({search.reason})"]
    if search.state == "off":
        lines.append(f"  {ALL_SCHEMAS}")
    lines.append(f"MCP servers for this project ({len(servers)}), calls in the last {days} days:")
    for s in sorted(servers, key=lambda s: (s.scope, s.name.lower())):
        key = tool_name_server(s.name)
        calls_here, calls_all = here.get(key, 0), everywhere.get(key, 0)
        flags = "  alwaysLoad (tools always loaded)" if s.always_load else ""
        lines.append(
            f"  {s.name:<24} {s.scope:<12} {s.transport:<6} "
            f"{calls_here:>5} here, {calls_all:>5} in all projects{flags}"
        )
        lines += [f"    {text}" for text in _advice(s, calls_here, calls_all)]
    if not servers:
        lines.append("  none configured in ~/.claude.json, .mcp.json or managed-mcp.json")
    lines.append("Plus any servers from plugins and claude.ai connectors: see /mcp in Claude Code.")
    lines += [f"note: {n}" for n in notes]
    return lines


def _advice(s: Server, here: int, everywhere: int) -> list[str]:
    if s.disabled:
        return ["disabled for this project"]
    if s.scope == "organization":
        return ["managed by your organization"]
    if here:
        return []
    advice = [f"unused in this project: turn it off here with /mcp (select {s.name}, Disable)"]
    if s.scope != "user" or not everywhere:  # a user-scope server still used elsewhere is kept
        advice.append(f"or remove it: claude mcp remove {s.name} --scope {s.scope}")
    return advice
