"""Builders for Claude Code session records, shaped like real 2.1.2xx logs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

Record = dict[str, Any]

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
CWD = "C:/work/secret-repo"
VERSION = "2.1.290"


def ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _base(kind: str, minutes: float, cwd: str) -> Record:
    return {"type": kind, "timestamp": ts(minutes), "cwd": cwd, "version": VERSION, "sessionId": "s"}


def assistant(
    msg_id: str,
    *,
    minutes: float = 0.0,
    usage: tuple[int, int, int, int] = (10, 5, 0, 0),
    content: Sequence[Record] = (),
    model: str = "claude-sonnet-5",
    cwd: str = CWD,
) -> Record:
    """usage = (input, output, cache_read, cache_write)."""
    record = _base("assistant", minutes, cwd)
    record["message"] = {
        "id": msg_id,
        "role": "assistant",
        "model": model,
        "content": list(content) or [{"type": "text", "text": "ok"}],
        "usage": {
            "input_tokens": usage[0],
            "output_tokens": usage[1],
            "cache_read_input_tokens": usage[2],
            "cache_creation_input_tokens": usage[3],
        },
    }
    return record


def tool_use(tool_id: str, name: str, **inputs: Any) -> Record:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inputs}


def tool_result(tool_id: str, content: Any, *, minutes: float = 0.0, cwd: str = CWD) -> Record:
    record = _base("user", minutes, cwd)
    record["message"] = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
    }
    return record


def prompt(text: Any, *, minutes: float = 0.0, cwd: str = CWD, **flags: Any) -> Record:
    record = _base("user", minutes, cwd)
    record["message"] = {"role": "user", "content": text}
    record.update(flags)
    return record


def compact_boundary(*, minutes: float = 0.0) -> Record:
    record = _base("system", minutes, CWD)
    record["subtype"] = "compact_boundary"
    return record


def mcp_announce(*names: str, minutes: float = 0.0) -> Record:
    record = _base("attachment", minutes, CWD)
    record["attachment"] = {"type": "deferred_tools_delta", "addedNames": list(names), "removedNames": []}
    return record


def write_jsonl(path: Path, records: Iterable[Record], extra_lines: Iterable[str] = ()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
