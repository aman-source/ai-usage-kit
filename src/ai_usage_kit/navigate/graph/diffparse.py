"""Unified diff -> per-file changed lines on the OLD side (the indexed commit)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileChange:
    path: str
    new_file: bool = False
    deleted: bool = False
    removed: list[tuple[int, str]] = field(default_factory=list)  # (old line, text)
    inserted: list[tuple[int, str]] = field(default_factory=list)  # (old line it precedes, text)


def parse_diff(text: str) -> list[FileChange]:
    files: list[FileChange] = []
    cur: FileChange | None = None
    old = 0
    old_name = new_name = None
    in_hunk = False
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            in_hunk = False
            old_name = new_name = None
            cur = None
            m = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
            if m:
                cur = FileChange(m.group(2))
                files.append(cur)
            continue
        if not in_hunk:
            if raw.startswith("--- "):
                old_name = raw[4:].split("\t")[0]
                continue
            if raw.startswith("+++ "):
                new_name = raw[4:].split("\t")[0]
                if cur is None:  # plain `diff -u` without a git header
                    nm = new_name if new_name != "/dev/null" else (old_name or "")
                    cur = FileChange(re.sub(r"^[ab]/", "", nm))
                    files.append(cur)
                if old_name == "/dev/null":
                    cur.new_file = True
                if new_name == "/dev/null":
                    cur.deleted = True
                    if old_name:
                        cur.path = re.sub(r"^a/", "", old_name)
                continue
            if raw.startswith("new file mode") and cur:
                cur.new_file = True
                continue
            if raw.startswith("deleted file mode") and cur:
                cur.deleted = True
                continue
        m = HUNK.match(raw)
        if m and cur is not None:
            old = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk or cur is None:
            continue
        if raw.startswith("-"):
            cur.removed.append((old, raw[1:]))
            old += 1
        elif raw.startswith("+"):
            cur.inserted.append((old, raw[1:]))
        elif raw.startswith("\\"):
            continue
        else:
            old += 1
    return files
