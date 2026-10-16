"""Per-session, per-agent records of successful reads, one small file per read key.

Layout:
- read-once/<session>/<agent>/<key-hash>.json: size, mtime, hash, time and a denied flag for one file
  and range;
- read-once/<session>/<agent>/pending/<tool_use_id>.json: the fingerprint taken just before a read.
One file per key means parallel reads never overwrite each other's records. Never holds file content.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ai_usage_kit.hook_files import safe_id, write_private

KEEP_DAYS = 7
_FIELDS: dict[str, type | tuple[type, ...]] = {
    "size": int, "mtime": int, "sha256": str, "at": (int, float), "denied": bool,
}
_PENDING_FIELDS: dict[str, type | tuple[type, ...]] = {"size": int, "mtime": int, "sha256": str}


def root(state_dir: Path) -> Path:
    return state_dir / "read-once"


def counts_file(state_dir: Path) -> Path:
    return root(state_dir) / "counts.jsonl"


def agent_dir(state_dir: Path, session: object, agent: object) -> Path:
    return root(state_dir) / safe_id(session, "session") / safe_id(agent, "main")


def record_file(state_dir: Path, session: object, agent: object, key: str) -> Path:
    name = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return agent_dir(state_dir, session, agent) / f"{name}.json"


def pending_file(state_dir: Path, session: object, agent: object, tool_use_id: object) -> Path:
    return agent_dir(state_dir, session, agent) / "pending" / f"{safe_id(tool_use_id, 'read')}.json"


def load_record(path: Path) -> dict[str, Any] | None:
    return _load(path, _FIELDS)


def load_pending(path: Path) -> dict[str, Any] | None:
    return _load(path, _PENDING_FIELDS)


def save(path: Path, entry: dict[str, Any]) -> None:
    write_private(path, json.dumps(entry))


def forget_session(state_dir: Path, session: object) -> None:
    shutil.rmtree(root(state_dir) / safe_id(session, "session"), ignore_errors=True)


def expire(state_dir: Path, now: float) -> None:
    """Delete records and pending fingerprints older than KEEP_DAYS, then folders emptied by that."""
    base = root(state_dir)
    if not base.is_dir():
        return
    cutoff = now - KEEP_DAYS * 86400
    emptied: set[Path] = set()
    for path in base.rglob("*.json"):
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()
                emptied.add(path.parent)
    folders = sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for folder in folders:
        with contextlib.suppress(OSError):
            # A folder just emptied here, or one left empty for KEEP_DAYS; a fresh empty folder (a read in
            # progress) is left alone.
            if folder in emptied or folder.stat().st_mtime < cutoff:
                folder.rmdir()  # only succeeds when empty
                emptied.add(folder.parent)


def _load(path: Path, fields: dict[str, type | tuple[type, ...]]) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(isinstance(data.get(f), t) for f, t in fields.items()):
        return None
    return data
