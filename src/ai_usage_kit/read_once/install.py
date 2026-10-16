"""The hook entries that `eai on read-once` adds to Claude Code's user settings."""

from __future__ import annotations

from typing import Any

TIMEOUT_SECONDS = 10
MATCHERS = {"PreToolUse": "Read", "PostToolUse": "Read", "SessionStart": "compact|clear"}


def hook_groups(command: str) -> dict[str, dict[str, Any]]:
    handler = {"type": "command", "command": command, "timeout": TIMEOUT_SECONDS}
    return {event: {"matcher": matcher, "hooks": [dict(handler)]} for event, matcher in MATCHERS.items()}
