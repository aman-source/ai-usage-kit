"""Claude Code hooks for read-once.

PreToolUse(Read): deny once when this conversation context already read this file and range and the
file is unchanged; otherwise note the file's fingerprint just before the read.
PostToolUse(Read): record the read only if the file is still identical to that fingerprint, so the
record always matches what Claude actually saw.
SessionStart(compact|clear): forget the session.
Fails open: on any problem it says nothing and the read proceeds.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_usage_kit.hook_files import append_count
from ai_usage_kit.read_once import state

MAX_BYTES = 2 * 1024 * 1024
SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff", ".heic", ".pdf", ".ipynb",
})
REASON = (
    "{path} is unchanged since you read it earlier in this conversation (same lines). "
    "If that earlier result is still visible to you, use it. "
    "If it is not (cleared, rewound or compacted), read it again now and it will be allowed."
)


def run_hook(raw: bytes, state_dir: Path, now: float) -> bytes:
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return b""
        reply = handle(payload, state_dir, now)
        return json.dumps(reply).encode("utf-8") if reply else b""
    except Exception:  # fail open: the read proceeds
        return b""


def handle(payload: Mapping[str, Any], state_dir: Path, now: float) -> dict[str, Any] | None:
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        if payload.get("source") in ("compact", "clear"):
            state.forget_session(state_dir, payload.get("session_id"))
        return None
    if event not in ("PreToolUse", "PostToolUse") or payload.get("tool_name") != "Read":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(str(payload.get("cwd") or ".")) / path
    # One spelling per file: absolute, normalized separators, and (on Windows) case-folded.
    path = Path(os.path.normcase(os.path.abspath(path)))
    if path.suffix.lower() in SKIP_SUFFIXES:
        return None
    key = json.dumps([str(path), tool_input.get("offset"), tool_input.get("limit"), tool_input.get("pages")])
    session, agent = payload.get("session_id"), payload.get("agent_id")
    record_path = state.record_file(state_dir, session, agent, key)
    pending_path = state.pending_file(state_dir, session, agent, payload.get("tool_use_id"))
    fingerprint = _fingerprint(path)
    if event == "PostToolUse":
        pending = state.load_pending(pending_path)
        pending_path.unlink(missing_ok=True)
        if fingerprint is None or pending is None:
            return None
        if (pending["size"], pending["mtime"], pending["sha256"]) != fingerprint:
            return None  # the file changed while Claude read it: Claude may not have seen this content
        size, mtime, digest = fingerprint
        state.save(record_path, {"size": size, "mtime": mtime, "sha256": digest, "at": now, "denied": False})
        state.expire(state_dir, now)
        return None
    if fingerprint is None:
        return None
    entry = state.load_record(record_path)
    unchanged = entry is not None and (entry["size"], entry["mtime"], entry["sha256"]) == fingerprint
    if entry is not None and unchanged and not entry["denied"]:
        entry["denied"] = True
        state.save(record_path, entry)
        day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
        append_count(state.counts_file(state_dir), {"day": day, "before": entry["size"], "after": 0})
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON.format(path=file_path),
            }
        }
    size, mtime, digest = fingerprint
    state.save(pending_path, {"size": size, "mtime": mtime, "sha256": digest})
    return None


def _fingerprint(path: Path) -> tuple[int, int, str] | None:
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size > MAX_BYTES:
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns, digest
