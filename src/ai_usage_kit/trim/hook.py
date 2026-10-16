"""The PostToolUse hook: shorten a successful noisy command's output and keep the full output locally.

Fails open: on any problem it says nothing, so Claude sees the original output. Failed commands never
reach this hook (Claude Code reports them as PostToolUseFailure, which trim does not listen to).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_usage_kit import hook_files
from ai_usage_kit.trim.filters.pytest import NO_TESTS
from ai_usage_kit.trim.matching import match_command

THRESHOLD = 8000
MIN_SAVING = 0.30
KEEP_DAYS = 7
STATE_DIR_NAME = ".ai-usage-kit"


def state_dir(environ: Mapping[str, str], home: Path) -> Path:
    override = environ.get("EAI_STATE_DIR")
    return Path(override) if override else home / STATE_DIR_NAME


def run_hook(raw: bytes, state: Path, now: float) -> bytes:
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return b""
        reply = handle(payload, state, now)
        return json.dumps(reply).encode("utf-8") if reply else b""
    except Exception:  # fail open: Claude sees the original output
        return b""


def handle(payload: Mapping[str, Any], state: Path, now: float) -> dict[str, Any] | None:
    if payload.get("hook_event_name") != "PostToolUse" or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    response = payload.get("tool_response")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not isinstance(response, dict):
        return None
    stdout, stderr = response.get("stdout"), response.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return None
    if response.get("interrupted") or response.get("isImage"):
        return None
    if len(stdout) + len(stderr) <= THRESHOLD:
        return None
    match = match_command(command)
    if match is None:
        return None
    new_stdout = match.filter(stdout)
    new_stderr = stderr  # never filtered; Claude Code already merges stderr into stdout
    if not new_stdout.strip() or new_stdout.startswith(NO_TESTS):
        return None
    before, after = len(stdout) + len(stderr), len(new_stdout) + len(new_stderr)
    if after > (1 - MIN_SAVING) * before:
        return None
    saved = _save(state, payload, command, stdout, stderr, now)
    total = len(stdout.splitlines()) + len(stderr.splitlines())
    shown = len(new_stdout.splitlines()) + len(new_stderr.splitlines())
    new_stdout += f"\n[eai trim: showed {shown} of {total} lines; full output: {saved.as_posix()}]"
    _count(state, match.name, before, len(new_stdout) + len(new_stderr), now)
    updated = {**response, "stdout": new_stdout, "stderr": new_stderr}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": updated}}


def read_counts(state: Path, now: float, days: int = 30) -> tuple[int, int, int]:
    return hook_files.read_counts(state / "trim" / "counts.jsonl", now, days)


def _save(
    state: Path, payload: Mapping[str, Any], command: str, stdout: str, stderr: str, now: float
) -> Path:
    root = state / "trim"
    hook_files.private_dir(root)
    expire_logs(state, now)
    folder = root / hook_files.safe_id(payload.get("session_id"), "session")
    hook_files.private_dir(folder)
    path = folder / f"{hook_files.safe_id(payload.get('tool_use_id'), str(int(now * 1000)))}.log"
    path.write_text(f"$ {command}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def expire_logs(state: Path, now: float) -> None:
    """Delete saved full outputs older than KEEP_DAYS, and session folders left empty that long."""
    root = state / "trim"
    if not root.is_dir():
        return
    cutoff = now - KEEP_DAYS * 86400
    for log in root.glob("*/*.log"):
        try:
            if log.stat().st_mtime < cutoff:
                log.unlink()
        except OSError:
            continue
    for folder in root.iterdir():
        if folder.is_dir():
            with contextlib.suppress(OSError):
                if folder.stat().st_mtime < cutoff:
                    folder.rmdir()  # only succeeds when empty


def _count(state: Path, name: str, before: int, after: int, now: float) -> None:
    day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
    row: dict[str, object] = {"day": day, "command": name, "before": before, "after": after}
    hook_files.append_count(state / "trim" / "counts.jsonl", row)


def now_seconds() -> float:
    return time.time()
