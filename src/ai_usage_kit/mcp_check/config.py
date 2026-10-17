"""MCP servers that apply to a project, from local config files. Read-only; values are never kept.

Locations from code.claude.com/docs/en/mcp and /managed-mcp (checked 2026-10-16):
- user scope: ~/.claude.json `mcpServers`; local scope: ~/.claude.json `projects.<path>.mcpServers`;
- project scope: <project>/.mcp.json; organization: managed-mcp.json and the `managedMcpServers` setting.
Per-project choices in ~/.claude.json: `disabledMcpServers` (servers turned off in /mcp) and
`disabledMcpjsonServers` (.mcp.json servers rejected).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_usage_kit.defaults.managed import Managed


@dataclass(frozen=True)
class Server:
    name: str
    scope: str  # user | local | project | organization
    transport: str
    always_load: bool
    disabled: bool


@dataclass(frozen=True)
class Sources:
    home: Path
    environ: Mapping[str, str]
    project: Path
    managed: Managed
    managed_mcp: Path

    @property
    def config_dir(self) -> Path:
        custom = self.environ.get("CLAUDE_CONFIG_DIR")
        return Path(custom).expanduser() if custom else self.home / ".claude"

    @property
    def claude_json(self) -> Path:
        custom = self.environ.get("CLAUDE_CONFIG_DIR")
        return Path(custom).expanduser() / ".claude.json" if custom else self.home / ".claude.json"


def read_servers(src: Sources) -> tuple[list[Server], list[str]]:
    notes: list[str] = []
    servers: list[Server] = []
    claude = _read_json(src.claude_json, notes) or {}
    project_entry = _project_entry(claude, src.project)
    disabled = set(_strings(project_entry.get("disabledMcpServers")))
    servers += _entries(claude.get("mcpServers"), "user", disabled, f"{src.claude_json} mcpServers", notes)
    servers += _entries(project_entry.get("mcpServers"), "local", disabled, "local servers", notes)
    mcp_json = _read_json(src.project / ".mcp.json", notes) or {}
    rejected = set(_strings(project_entry.get("disabledMcpjsonServers")))
    servers += _entries(mcp_json.get("mcpServers"), "project", rejected, ".mcp.json", notes)
    managed_file = _read_json(src.managed_mcp, notes) or {}
    servers += _entries(managed_file.get("mcpServers"), "organization", set(), "managed-mcp.json", notes)
    servers += _entries(
        src.managed.get("managedMcpServers"), "organization", set(), "managedMcpServers", notes
    )
    return servers, notes


def settings_env_blocks(src: Sources) -> list[Mapping[str, object]]:
    """`env` blocks of user, project, local and managed settings, lowest precedence first."""
    blocks: list[Mapping[str, object]] = []
    for path in (
        src.config_dir / "settings.json",
        src.project / ".claude" / "settings.json",
        src.project / ".claude" / "settings.local.json",
    ):
        data = _read_json(path, [])
        env = data.get("env") if data else None
        if isinstance(env, dict):
            blocks.append(env)
    managed_env = src.managed.get("env")
    if isinstance(managed_env, dict):
        blocks.append(managed_env)
    return blocks


def _read_json(path: Path, notes: list[str]) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        notes.append(f"could not read {path}: {getattr(error, 'strerror', None) or error}")
        return None
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        notes.append(f"{path} is not valid JSON; skipped")
        return None
    if not isinstance(data, dict):
        notes.append(f"{path} is not a JSON object; skipped")
        return None
    return data


def _key(path: str) -> str:
    return os.path.normcase(path.replace("\\", "/").rstrip("/"))


def _project_entry(claude: Mapping[str, Any], project: Path) -> Mapping[str, Any]:
    projects = claude.get("projects")
    if not isinstance(projects, dict):
        return {}
    wanted = _key(str(project))
    for name, entry in projects.items():
        if isinstance(entry, dict) and _key(name) == wanted:
            return entry
    return {}


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _entries(value: object, scope: str, disabled: set[str], label: str, notes: list[str]) -> list[Server]:
    if value is None:
        return []
    if not isinstance(value, dict):
        notes.append(f"{label} is not a JSON object; skipped")
        return []
    found: list[Server] = []
    for name, entry in value.items():
        if not isinstance(entry, dict):
            notes.append(f"{label}: server {name!r} is not a JSON object; skipped")
            continue
        kind = entry.get("type")
        transport = kind if isinstance(kind, str) else ("stdio" if "command" in entry else "unknown")
        found.append(Server(name, scope, transport, entry.get("alwaysLoad") is True, name in disabled))
    return found
