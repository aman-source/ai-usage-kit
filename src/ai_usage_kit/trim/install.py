"""The hook entry that `eai on trim` adds to Claude Code's user settings."""

from __future__ import annotations

import re
from pathlib import PureWindowsPath
from typing import Any

from ai_usage_kit.trim.matching import PREFIXES

# Claude Code starts the hook only for these commands (permission-rule syntax). They come from the same
# prefixes the hook accepts; a prefix already covered by a shorter one is left out, so no command runs twice.
IF_RULES = tuple(
    f"Bash({prefix}*)"
    for prefix in PREFIXES
    if not any(other != prefix and prefix.startswith(other) for other in PREFIXES)
)
TIMEOUT_SECONDS = 10


def hook_command_for(executable: str, name: str = "trim") -> str:
    """Quote the eai executable with forward slashes and shell-escape it, then name the hook."""
    path = PureWindowsPath(executable).as_posix()
    return '"' + re.sub(r'(["$`\\])', r"\\\1", path) + f'" hook {name}'


def is_persistent_install(executable: str) -> bool:
    """False for copies that disappear: a project or virtual environment, or uv's cache (uvx)."""
    parts = [part for part in re.split(r"[\\/]+", executable.lower()) if part]
    joined = "/" + "/".join(parts) + "/"
    if {".venv", "venv", "site-packages", "archive-v0"}.intersection(parts):
        return False
    return "/uv/cache/" not in joined and "/.cache/uv/" not in joined


def hook_group(command: str) -> dict[str, Any]:
    handlers = [
        {"type": "command", "if": rule, "command": command, "timeout": TIMEOUT_SECONDS} for rule in IF_RULES
    ]
    return {"matcher": "Bash", "hooks": handlers}


def hook_groups(command: str) -> dict[str, dict[str, Any]]:
    return {"PostToolUse": hook_group(command)}
