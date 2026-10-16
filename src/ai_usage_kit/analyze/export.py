"""Numbers-only export for an org baseline.

No paths, names, prompts, commands, fine timestamps or persistent identifiers: each export gets a fresh random
id, so exports from one person cannot be linked to each other by the tool.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ai_usage_kit.analyze.model import mcp_server, total_usage
from ai_usage_kit.analyze.rules import rule_totals
from ai_usage_kit.analyze.runner import Analysis

SCHEMA_VERSION = 1
# Optional Bedrock region/vendor prefix and version suffix, Vertex "@date" and context-size "[1m]" suffixes.
_MODEL = re.compile(
    r"(?:[a-z]{2,4}\.)?(?:anthropic\.)?"  # Bedrock region and vendor prefix
    r"(claude-[a-z0-9.-]+?)"
    r"(?:-v\d+(?::\d+)?)?(?:@\d+)?(?:\[1m\])?"  # Bedrock version, Vertex date, 1M-context suffix
)
# Claude Code built-in tool names. Anything else (custom or SDK-host tools) is exported as "other".
BUILTIN_TOOLS = frozenset(
    {
        "Agent", "Artifact", "AskUserQuestion", "Bash", "BashOutput", "Edit", "EnterPlanMode",
        "ExitPlanMode", "Glob", "Grep", "KillShell", "LS", "Monitor", "MultiEdit", "NotebookEdit",
        "NotebookRead", "PowerShell", "PushNotification", "Read", "ScheduleWakeup", "SendUserFile", "Skill",
        "SubagentHandback", "Task", "TaskStop", "TodoWrite", "ToolSearch", "WebFetch", "WebSearch", "Write",
    }
)
_VERSION = re.compile(r"\d+(\.\d+)*")


def export_model_name(model: str) -> str:
    """A plain Claude model name, or "other" for anything else (cloud ARNs carry account ids)."""
    match = _MODEL.fullmatch(model)
    return match.group(1) if match else "other"


def build_export(a: Analysis, tool_version: str) -> dict[str, object]:
    starts = sorted(start for s in a.sessions if (start := s.start) is not None)
    tools: Counter[str] = Counter()
    models: Counter[str] = Counter()
    for s in a.sessions:
        for call in s.tool_calls:
            if mcp_server(call.name) is None:
                tools[call.name if call.name in BUILTIN_TOOLS else "other"] += 1
        for api in s.calls:
            models[export_model_name(api.model)] += 1
    servers = {name for s in a.sessions for name in s.mcp_servers}
    return {
        "schema": SCHEMA_VERSION,
        "tool_version": tool_version,
        "export_id": uuid.uuid4().hex,
        "date_from": f"{starts[0]:%Y-%m-%d}" if starts else None,
        "date_to": f"{starts[-1]:%Y-%m-%d}" if starts else None,
        "sessions": len(a.sessions),
        "api_calls": sum(len(s.calls) for s in a.sessions),
        "user_prompts": sum(s.user_prompts for s in a.sessions),
        "compactions": sum(s.compactions for s in a.sessions),
        "tokens": {
            "main": asdict(total_usage(a.sessions, "main")),
            "sub": asdict(total_usage(a.sessions, "sub")),
            "all": asdict(total_usage(a.sessions)),
        },
        "rules": {
            rule: {"hits": hits, "exact_tokens": exact, "est_tokens": est}
            for rule, (hits, exact, est) in rule_totals(a.findings).items()
        },
        "claude_code_versions": sorted(v for v in a.stats.versions if _VERSION.fullmatch(v)),
        "tools": dict(sorted(tools.items())),
        "models": dict(sorted(models.items())),
        "mcp_server_count": len(servers),
        "parse": {
            "files": a.stats.files,
            "unreadable_files": len(a.stats.unreadable),
            "bad_lines": a.stats.bad_lines,
            "unknown_records": sum(a.stats.unknown_types.values()),
        },
    }


def write_export(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
