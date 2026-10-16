"""Small helpers for the private files Claude Code hooks keep: safe names, private folders, atomic writes and
numbers-only count rows."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def safe_id(value: object, fallback: str) -> str:
    text = _UNSAFE.sub("_", value if isinstance(value, str) else "")[:100].strip("_")
    return text or fallback


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def write_private(path: Path, text: str) -> None:
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)


def append_count(path: Path, row: dict[str, object]) -> None:
    private_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    os.chmod(path, 0o600)


def read_counts(path: Path, now: float, days: int = 30) -> tuple[int, int, int]:
    cutoff = datetime.fromtimestamp(now - days * 86400, UTC).strftime("%Y-%m-%d")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0, 0
    rows = before = after = 0
    for line in lines:
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or str(row.get("day", "")) < cutoff:
                continue
            row_before, row_after = int(row.get("before", 0)), int(row.get("after", 0))
        except (ValueError, TypeError):
            continue
        rows += 1
        before += row_before
        after += row_after
    return rows, before, after


def prune_counts(path: Path, now: float, keep_days: int = 90) -> None:
    """Drop count rows older than keep_days so the file cannot grow without bound."""
    cutoff = datetime.fromtimestamp(now - keep_days * 86400, UTC).strftime("%Y-%m-%d")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    kept = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("day", "")) >= cutoff:
            kept.append(line)
    if len(kept) != len(lines):
        write_private(path, "".join(f"{line}\n" for line in kept))
